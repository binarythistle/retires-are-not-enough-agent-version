"""The plain agent loop. Everything it knows lives in this process's memory: the transcript,
the journey state, and how far through a turn it got. Kill the process and all of it is gone.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import httpx
from openai import AsyncOpenAI

from conference_booker.agent import policy, state_tools, tools
from conference_booker.agent.model import (
    ModelToolCall,
    assistant_message,
    invoke_model,
    system_message,
    tool_calls_message,
    tool_result_message,
    user_message,
)
from conference_booker.agent.results import (
    CatalogueToolResult,
    ConferenceBookingResult,
    FlightBookingResult,
    FlightSearchResult,
)
from conference_booker.catalog import ConferenceCatalogClient, conference_city
from conference_booker.flights import FlightSearchClient
from conference_booker.journey import (
    ALREADY_COMPLETE,
    WELCOME_PROMPT,
    AgentTurnReply,
    ConversationRoles,
    ToolActivity,
)
from conference_booker.models import DomainModel
from plain_agent.store import Conversation

logger = logging.getLogger(__name__)

CATALOGUE_UNAVAILABLE = "The Conference Catalog API is currently unavailable."
FLIGHT_SEARCH_UNAVAILABLE = "The Flight Search API is currently unavailable."
FLIGHT_BOOKING_UNAVAILABLE = "The Flight Booking API is currently unavailable."


class ConferenceBookerAgent:
    def __init__(
        self,
        openai: AsyncOpenAI,
        model: str,
        catalog: ConferenceCatalogClient,
        flights: FlightSearchClient,
    ) -> None:
        self._openai = openai
        self._model = model
        self._catalog = catalog
        self._flights = flights

    async def start(self, conversation: Conversation) -> AgentTurnReply:
        return await self._run_turn(conversation, WELCOME_PROMPT, record_user_message=False)

    async def run(self, conversation: Conversation, message: str) -> AgentTurnReply:
        return await self._run_turn(conversation, message, record_user_message=True)

    async def _run_turn(self, conversation: Conversation, message: str, record_user_message: bool) -> AgentTurnReply:
        async with conversation.gate:
            if conversation.state.is_complete:
                conversation.state = conversation.state.record(message, ALREADY_COMPLETE, record_user_message, [])
                return AgentTurnReply(message=ALREADY_COMPLETE, tool_activities=[], is_complete=True)

            state = conversation.state
            messages = [system_message(policy.build_system_prompt(state))]
            messages += [
                user_message(entry.message) if entry.role == ConversationRoles.USER else assistant_message(entry.message)
                for entry in state.entries
            ]
            messages.append(user_message(message))

            required_tool = policy.get_required_tool(state, message)
            tool_activities: list[ToolActivity] = []

            for step in range(policy.MAXIMUM_STEPS):
                response = await invoke_model(self._openai, self._model, messages, required_tool)
                conversation.state = conversation.state.model_copy(
                    update={"tokens_used": conversation.state.tokens_used.add(response.usage)}
                )

                # A forced tool applies to the first step only; afterwards the model is free.
                required_tool = None

                if not response.tool_calls:
                    reply = (
                        policy.create_completion_summary(conversation.state)
                        if conversation.state.is_complete
                        else response.text or policy.NO_REPLY
                    )
                    logger.info(
                        "Turn complete after %d model round-trip(s); journey tokens: %d",
                        step + 1,
                        conversation.state.tokens_used.total_tokens,
                    )
                    return self._finish(conversation, message, reply, record_user_message, tool_activities)

                messages.append(tool_calls_message(response.tool_calls))

                for call in response.tool_calls:
                    started = time.perf_counter()
                    result_json, status = await self._dispatch_tool(conversation, call)
                    tool_activities.append(
                        ToolActivity(
                            name=call.name,
                            status=status,
                            duration_milliseconds=round((time.perf_counter() - started) * 1000),
                        )
                    )
                    messages.append(tool_result_message(call.call_id, result_json))

            return self._finish(conversation, message, policy.STEPS_EXHAUSTED, record_user_message, tool_activities)

    @staticmethod
    def _finish(
        conversation: Conversation,
        message: str,
        reply: str,
        record_user_message: bool,
        tool_activities: list[ToolActivity],
    ) -> AgentTurnReply:
        conversation.state = conversation.state.record(message, reply, record_user_message, tool_activities)
        return AgentTurnReply(
            message=reply, tool_activities=tool_activities, is_complete=conversation.state.is_complete
        )

    async def _dispatch_tool(self, conversation: Conversation, call: ModelToolCall) -> tuple[str, str]:
        """Runs one tool and returns what the model should see plus the status for the UI."""
        match call.name:
            case tools.GET_CURRENT_DATE_TIME:
                now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                return json.dumps(now), "succeeded"

            case tools.GET_AVAILABLE_CONFERENCES:
                try:
                    conferences = await self._catalog.get_available_conferences()
                except httpx.HTTPError as exception:
                    logger.warning("Conference Catalog API call failed: %r", exception)
                    return _failed(CatalogueToolResult(conferences=[], error=CATALOGUE_UNAVAILABLE))

                conversation.state = conversation.state.model_copy(update={"available_conferences": conferences})
                return _succeeded(CatalogueToolResult(conferences=conferences, error=None))

            case tools.SAVE_TRAVELLER_DETAILS:
                arguments = _arguments(call)
                conversation.state, result = state_tools.save_traveller_details(
                    conversation.state,
                    arguments.get("travellerName", ""),
                    arguments.get("departureLocation", ""),
                    arguments.get("travelPreference", ""),
                )
                return _succeeded(result) if result.saved else _failed(result)

            case tools.SELECT_CONFERENCE:
                conversation.state, result = state_tools.select_conference(
                    conversation.state, _arguments(call).get("conferenceId", "")
                )
                return _succeeded(result) if result.selected else _failed(result)

            case tools.BOOK_SELECTED_CONFERENCE:
                return await self._book_selected_conference(conversation)

            case tools.GET_FLIGHT_OPTIONS:
                return await self._get_flight_options(conversation)

            case tools.SELECT_FLIGHT:
                conversation.state, result = state_tools.select_flight(
                    conversation.state, _arguments(call).get("flightId", "")
                )
                return _succeeded(result) if result.selected else _failed(result)

            case tools.BOOK_SELECTED_FLIGHT:
                return await self._book_selected_flight(conversation)

            case _:
                return json.dumps({"error": f"Unknown tool '{call.name}'."}), "failed"


    # The bookings deliberately send no idempotency key. If the process dies after the API
    # confirms but before the state below is updated, the booking exists but this agent has
    # forgotten it, and booking again makes a duplicate. That is the plain version's weakness.

    async def _book_selected_conference(self, conversation: Conversation) -> tuple[str, str]:
        state = conversation.state
        if state.traveller_details is None or state.selected_conference is None:
            return _failed(
                ConferenceBookingResult(
                    booked=False, booking=None, error="Save traveller details and select a conference before booking."
                )
            )

        if state.conference_booking is not None:
            return _succeeded(ConferenceBookingResult(booked=True, booking=state.conference_booking, error=None))

        try:
            booking = await self._catalog.book_conference(
                state.selected_conference.id, state.traveller_details.traveller_name
            )
        except httpx.HTTPError as exception:
            logger.warning("Conference booking failed: %r", exception)
            return _failed(ConferenceBookingResult(booked=False, booking=None, error=CATALOGUE_UNAVAILABLE))

        conversation.state = conversation.state.model_copy(update={"conference_booking": booking})
        return _succeeded(ConferenceBookingResult(booked=True, booking=booking, error=None))

    async def _get_flight_options(self, conversation: Conversation) -> tuple[str, str]:
        state = conversation.state
        if state.selected_conference is None or state.traveller_details is None or state.conference_booking is None:
            return _failed(
                FlightSearchResult(
                    found=False,
                    origin=None,
                    destination=None,
                    flights=[],
                    error="Book a selected conference and save traveller details before searching for flights.",
                )
            )

        origin = state.traveller_details.departure_location
        destination = conference_city(state.selected_conference.location)

        try:
            flights = await self._flights.search_flights(origin, destination)
        except httpx.HTTPError as exception:
            logger.warning("Flight search failed: %r", exception)
            return _failed(
                FlightSearchResult(
                    found=False, origin=origin, destination=destination, flights=[], error=FLIGHT_SEARCH_UNAVAILABLE
                )
            )

        conversation.state = conversation.state.model_copy(update={"available_flights": flights})

        # An empty result is a successful search, not a tool failure.
        if not flights:
            return _succeeded(
                FlightSearchResult(
                    found=False,
                    origin=origin,
                    destination=destination,
                    flights=[],
                    error=f"No flights were found from {origin} to {destination}.",
                )
            )
        return _succeeded(
            FlightSearchResult(found=True, origin=origin, destination=destination, flights=flights, error=None)
        )

    async def _book_selected_flight(self, conversation: Conversation) -> tuple[str, str]:
        state = conversation.state
        if state.traveller_details is None or state.selected_flight is None or state.conference_booking is None:
            return _failed(
                FlightBookingResult(
                    booked=False,
                    booking=None,
                    error="Book a conference, save traveller details, and select a flight before booking.",
                )
            )

        if state.flight_booking is not None:
            conversation.state = state.model_copy(update={"is_complete": True})
            return _succeeded(FlightBookingResult(booked=True, booking=state.flight_booking, error=None))

        try:
            booking = await self._flights.book_flight(state.selected_flight.id, state.traveller_details.traveller_name)
        except httpx.HTTPError as exception:
            logger.warning("Flight booking failed: %r", exception)
            return _failed(FlightBookingResult(booked=False, booking=None, error=FLIGHT_BOOKING_UNAVAILABLE))

        conversation.state = conversation.state.model_copy(update={"flight_booking": booking, "is_complete": True})
        return _succeeded(FlightBookingResult(booked=True, booking=booking, error=None))


def _arguments(call: ModelToolCall) -> dict[str, str]:
    try:
        parsed = json.loads(call.arguments_json or "{}")
    except json.JSONDecodeError:
        return {}
    return {key: value for key, value in parsed.items() if isinstance(value, str)} if isinstance(parsed, dict) else {}


def _succeeded(result: DomainModel) -> tuple[str, str]:
    return result.to_tool_json(), "succeeded"


def _failed(result: DomainModel) -> tuple[str, str]:
    return result.to_tool_json(), "failed"
