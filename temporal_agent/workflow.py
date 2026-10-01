"""One workflow execution per conversation. The workflow owns the journey state and the agent
loop, so every model round-trip and every tool call is a separate durable event. Kill the
worker mid-turn and a new one replays the history and carries on from the same point.

The loop is the plain agent's loop. The differences are where things run:
- a model call or an API call is an activity, so its result is recorded and retried;
- the clock is workflow.now(), the replay-safe clock;
- the three state tools are pure, so they run inline and replay for free.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from conference_booker.agent import policy, state_tools, tools
    from conference_booker.agent.model import (
        ModelToolCall,
        assistant_message,
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
    from conference_booker.catalog import conference_city
    from conference_booker.journey import (
        ALREADY_COMPLETE,
        WELCOME_PROMPT,
        AgentTurnReply,
        ConversationRoles,
        ConversationTranscript,
        JourneyState,
        TokenUsage,
        ToolActivity,
    )
    from conference_booker.models import DomainModel
    from temporal_agent.activities import (
        BookConferenceInput,
        BookFlightInput,
        ConferenceBookerActivities,
        ModelRequest,
        SearchFlightsInput,
    )

MODEL_TIMEOUT = timedelta(seconds=60)
# The model activity heartbeats every second, so a worker killed mid-call is detected and the
# call retried on another worker after this long, rather than after the full MODEL_TIMEOUT.
MODEL_HEARTBEAT_TIMEOUT = timedelta(seconds=5)

# API calls retry until the API comes back. Slice 6 adds non-retryable classification so a
# permanently bad request fails fast instead of retrying forever.
API_TIMEOUT = timedelta(seconds=15)
API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=10),
)


@workflow.defn
class ConferenceBookerWorkflow:
    @workflow.init
    def __init__(self, carried_state: JourneyState | None) -> None:
        # Initialised here rather than in run() so state exists before any update or query.
        self._state = carried_state or JourneyState()
        # Update handlers interleave at await points, so two quick submits would otherwise
        # each run a turn against the same state.
        self._turn_gate = asyncio.Lock()

    @workflow.run
    async def run(self, carried_state: JourneyState | None) -> None:
        await workflow.wait_condition(lambda: self._state.is_complete)
        # Let the final turn's update return its reply before the workflow closes.
        await workflow.wait_condition(workflow.all_handlers_finished)

    @workflow.update
    async def welcome(self) -> AgentTurnReply:
        """Opens the conversation. The prompt is not recorded as an attendee message."""
        return await self._run_turn(WELCOME_PROMPT, record_user_message=False)

    @workflow.update
    async def send_message(self, message: str) -> AgentTurnReply:
        """Handles one attendee message and returns the agent's reply."""
        return await self._run_turn(message, record_user_message=True)

    @send_message.validator
    def validate_send_message(self, message: str) -> None:
        if not message.strip():
            raise ValueError("A message is required.")

    @workflow.query
    def get_transcript(self) -> ConversationTranscript:
        return ConversationTranscript(messages=self._state.entries, is_complete=self._state.is_complete)

    @workflow.query
    def get_token_usage(self) -> TokenUsage:
        return self._state.tokens_used

    async def _run_turn(self, message: str, record_user_message: bool) -> AgentTurnReply:
        async with self._turn_gate:
            if self._state.is_complete:
                self._state = self._state.record(message, ALREADY_COMPLETE, record_user_message, [])
                return AgentTurnReply(message=ALREADY_COMPLETE, tool_activities=[], is_complete=True)

            state = self._state
            messages = [system_message(policy.build_system_prompt(state))]
            messages += [
                user_message(entry.message) if entry.role == ConversationRoles.USER else assistant_message(entry.message)
                for entry in state.entries
            ]
            messages.append(user_message(message))

            required_tool = policy.get_required_tool(state, message)
            tool_activities: list[ToolActivity] = []

            for step in range(policy.MAXIMUM_STEPS):
                response = await workflow.execute_activity_method(
                    ConferenceBookerActivities.invoke_model,
                    ModelRequest(messages=messages, required_tool=required_tool),
                    start_to_close_timeout=MODEL_TIMEOUT,
                    heartbeat_timeout=MODEL_HEARTBEAT_TIMEOUT,
                )
                self._state = self._state.model_copy(
                    update={"tokens_used": self._state.tokens_used.add(response.usage)}
                )

                # A forced tool applies to the first step only; afterwards the model is free.
                required_tool = None

                if not response.tool_calls:
                    reply = (
                        policy.create_completion_summary(self._state)
                        if self._state.is_complete
                        else response.text or policy.NO_REPLY
                    )
                    workflow.logger.info(
                        "Turn complete after %d model round-trip(s); journey tokens: %d",
                        step + 1,
                        self._state.tokens_used.total_tokens,
                    )
                    return self._finish(message, reply, record_user_message, tool_activities)

                messages.append(tool_calls_message(response.tool_calls))

                for call in response.tool_calls:
                    started = workflow.now()
                    result_json, status = await self._dispatch_tool(call)
                    tool_activities.append(
                        ToolActivity(
                            name=call.name,
                            status=status,
                            duration_milliseconds=round((workflow.now() - started).total_seconds() * 1000),
                        )
                    )
                    messages.append(tool_result_message(call.call_id, result_json))

            return self._finish(message, policy.STEPS_EXHAUSTED, record_user_message, tool_activities)

    def _finish(
        self,
        message: str,
        reply: str,
        record_user_message: bool,
        tool_activities: list[ToolActivity],
    ) -> AgentTurnReply:
        self._state = self._state.record(message, reply, record_user_message, tool_activities)
        return AgentTurnReply(message=reply, tool_activities=tool_activities, is_complete=self._state.is_complete)

    async def _dispatch_tool(self, call: ModelToolCall) -> tuple[str, str]:
        """Runs one tool and returns what the model should see plus the status for the UI.

        Only the four tools that call an external API are activities. The rest are pure
        functions of journey state and run inline.
        """
        match call.name:
            case tools.GET_CURRENT_DATE_TIME:
                # workflow.now() is the replay-safe clock; datetime.now() here would differ on replay.
                return json.dumps(workflow.now().isoformat().replace("+00:00", "Z")), "succeeded"

            case tools.GET_AVAILABLE_CONFERENCES:
                conferences = await workflow.execute_activity_method(
                    ConferenceBookerActivities.get_available_conferences,
                    start_to_close_timeout=API_TIMEOUT,
                    retry_policy=API_RETRY,
                )
                self._state = self._state.model_copy(update={"available_conferences": conferences})
                return _succeeded(CatalogueToolResult(conferences=conferences, error=None))

            case tools.SAVE_TRAVELLER_DETAILS:
                arguments = _arguments(call)
                self._state, result = state_tools.save_traveller_details(
                    self._state,
                    arguments.get("travellerName", ""),
                    arguments.get("departureLocation", ""),
                    arguments.get("travelPreference", ""),
                )
                return _succeeded(result) if result.saved else _failed(result)

            case tools.SELECT_CONFERENCE:
                self._state, result = state_tools.select_conference(
                    self._state, _arguments(call).get("conferenceId", "")
                )
                return _succeeded(result) if result.selected else _failed(result)

            case tools.BOOK_SELECTED_CONFERENCE:
                return await self._book_selected_conference()

            case tools.GET_FLIGHT_OPTIONS:
                return await self._get_flight_options()

            case tools.SELECT_FLIGHT:
                self._state, result = state_tools.select_flight(self._state, _arguments(call).get("flightId", ""))
                return _succeeded(result) if result.selected else _failed(result)

            case tools.BOOK_SELECTED_FLIGHT:
                return await self._book_selected_flight()

            case _:
                return json.dumps({"error": f"Unknown tool '{call.name}'."}), "failed"

    async def _book_selected_conference(self) -> tuple[str, str]:
        state = self._state
        if state.traveller_details is None or state.selected_conference is None:
            return _failed(
                ConferenceBookingResult(
                    booked=False, booking=None, error="Save traveller details and select a conference before booking."
                )
            )

        if state.conference_booking is not None:
            return _succeeded(ConferenceBookingResult(booked=True, booking=state.conference_booking, error=None))

        booking = await workflow.execute_activity_method(
            ConferenceBookerActivities.book_conference,
            BookConferenceInput(
                conference_id=state.selected_conference.id,
                traveller_name=state.traveller_details.traveller_name,
            ),
            start_to_close_timeout=API_TIMEOUT,
            retry_policy=API_RETRY,
        )
        self._state = self._state.model_copy(update={"conference_booking": booking})
        return _succeeded(ConferenceBookingResult(booked=True, booking=booking, error=None))

    async def _get_flight_options(self) -> tuple[str, str]:
        state = self._state
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

        flights = await workflow.execute_activity_method(
            ConferenceBookerActivities.search_flights,
            SearchFlightsInput(origin=origin, destination=destination),
            start_to_close_timeout=API_TIMEOUT,
            retry_policy=API_RETRY,
        )
        self._state = self._state.model_copy(update={"available_flights": flights})

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

    async def _book_selected_flight(self) -> tuple[str, str]:
        state = self._state
        if state.traveller_details is None or state.selected_flight is None or state.conference_booking is None:
            return _failed(
                FlightBookingResult(
                    booked=False,
                    booking=None,
                    error="Book a conference, save traveller details, and select a flight before booking.",
                )
            )

        if state.flight_booking is not None:
            self._state = state.model_copy(update={"is_complete": True})
            return _succeeded(FlightBookingResult(booked=True, booking=state.flight_booking, error=None))

        booking = await workflow.execute_activity_method(
            ConferenceBookerActivities.book_flight,
            BookFlightInput(flight_id=state.selected_flight.id, traveller_name=state.traveller_details.traveller_name),
            start_to_close_timeout=API_TIMEOUT,
            retry_policy=API_RETRY,
        )
        self._state = self._state.model_copy(update={"flight_booking": booking, "is_complete": True})
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
