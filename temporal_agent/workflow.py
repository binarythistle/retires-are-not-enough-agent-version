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
from temporalio.exceptions import ActivityError, is_cancelled_exception

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

# Retryable API failures (429, 5xx, network) retry until the API comes back, so they never
# reach the workflow. Permanent ones (other 4xx) are raised non-retryable by the activity and
# fail fast; the workflow turns them into a tool result the model can explain.
API_TIMEOUT = timedelta(seconds=15)
API_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=10),
)


# An abandoned booth conversation should not stay open for ever.
IDLE_TIMEOUT = timedelta(hours=1)

# Turns after which the run is rolled over. is_continue_as_new_suggested() is the signal to use
# in production; this lower bound also makes the behaviour reachable in a demo.
TURNS_BEFORE_CONTINUE_AS_NEW = 25

ABANDONED_IDLE = "This journey was closed after a period of inactivity. Start a new journey to book again."
ABANDONED_CANCELLED = "This journey was closed. Start a new journey to book again."

CATALOGUE_UNAVAILABLE = (
    "The Conference Catalog API could not complete that request. Tell the attendee it is "
    "unavailable right now and offer to try again shortly. Do not invent conference details."
)
FLIGHT_SEARCH_UNAVAILABLE = (
    "The Flight Search API could not complete that request. Tell the attendee it is "
    "unavailable right now and offer to try again shortly. Do not invent flight schedules."
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
        self._turn_count = 0
        self._abandoned = False

    @workflow.run
    async def run(self, carried_state: JourneyState | None) -> None:
        try:
            while not self._state.is_complete:
                if self._should_continue_as_new():
                    # Let the turn in flight finish before rolling the run over, otherwise its
                    # caller never receives a result.
                    await workflow.wait_condition(workflow.all_handlers_finished)
                    workflow.continue_as_new(self._state)

                turns_so_far = self._turn_count
                try:
                    await workflow.wait_condition(
                        lambda: self._state.is_complete or self._turn_count != turns_so_far,
                        timeout=IDLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    workflow.logger.info("Journey abandoned after %s with no attendee activity", IDLE_TIMEOUT)
                    # A conference ticket already booked is left alone: it was asked for deliberately.
                    self._record_abandonment(ABANDONED_IDLE)
                    return

            # Let the final turn's update return its reply before the workflow closes.
            await workflow.wait_condition(workflow.all_handlers_finished)
        except asyncio.CancelledError:
            # The attendee started a new journey, so this one is deliberately discarded. Record
            # why, then re-raise so the run shows as Cancelled rather than Completed.
            workflow.logger.info("Journey cancelled by the attendee")
            self._record_abandonment(ABANDONED_CANCELLED)
            raise

    def _should_continue_as_new(self) -> bool:
        return self._turn_count > 0 and (
            workflow.info().is_continue_as_new_suggested() or self._turn_count >= TURNS_BEFORE_CONTINUE_AS_NEW
        )

    def _record_abandonment(self, message: str) -> None:
        self._abandoned = True
        self._state = self._state.record("", message, record_user_message=False, tool_activities=[])

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
        return ConversationTranscript(
            messages=self._state.entries, is_complete=self._state.is_complete or self._abandoned
        )

    @workflow.query
    def get_token_usage(self) -> TokenUsage:
        return self._state.tokens_used

    async def _run_turn(self, message: str, record_user_message: bool) -> AgentTurnReply:
        async with self._turn_gate:
            self._turn_count += 1

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
                try:
                    conferences = await workflow.execute_activity_method(
                        ConferenceBookerActivities.get_available_conferences,
                        start_to_close_timeout=API_TIMEOUT,
                        retry_policy=API_RETRY,
                    )
                except ActivityError as error:
                    if is_cancelled_exception(error):
                        raise
                    return _api_failed(error, call, CatalogueToolResult(conferences=[], error=CATALOGUE_UNAVAILABLE))
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
                return await self._book_selected_conference(call)

            case tools.GET_FLIGHT_OPTIONS:
                return await self._get_flight_options(call)

            case tools.SELECT_FLIGHT:
                self._state, result = state_tools.select_flight(self._state, _arguments(call).get("flightId", ""))
                return _succeeded(result) if result.selected else _failed(result)

            case tools.BOOK_SELECTED_FLIGHT:
                return await self._book_selected_flight()

            case _:
                return json.dumps({"error": f"Unknown tool '{call.name}'."}), "failed"

    async def _book_selected_conference(self, call: ModelToolCall) -> tuple[str, str]:
        state = self._state
        if state.traveller_details is None or state.selected_conference is None:
            return _failed(
                ConferenceBookingResult(
                    booked=False, booking=None, error="Save traveller details and select a conference before booking."
                )
            )

        if state.conference_booking is not None:
            return _succeeded(ConferenceBookingResult(booked=True, booking=state.conference_booking, error=None))

        try:
            booking = await workflow.execute_activity_method(
                ConferenceBookerActivities.book_conference,
                BookConferenceInput(
                    conference_id=state.selected_conference.id,
                    traveller_name=state.traveller_details.traveller_name,
                    idempotency_key=_idempotency_key("conference", state.selected_conference.id),
                ),
                start_to_close_timeout=API_TIMEOUT,
                retry_policy=API_RETRY,
            )
        except ActivityError as error:
            if is_cancelled_exception(error):
                raise
            return _api_failed(
                error, call, ConferenceBookingResult(booked=False, booking=None, error=CATALOGUE_UNAVAILABLE)
            )
        self._state = self._state.model_copy(update={"conference_booking": booking})
        return _succeeded(ConferenceBookingResult(booked=True, booking=booking, error=None))

    async def _get_flight_options(self, call: ModelToolCall) -> tuple[str, str]:
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

        try:
            flights = await workflow.execute_activity_method(
                ConferenceBookerActivities.search_flights,
                SearchFlightsInput(origin=origin, destination=destination),
                start_to_close_timeout=API_TIMEOUT,
                retry_policy=API_RETRY,
            )
        except ActivityError as error:
            if is_cancelled_exception(error):
                raise
            return _api_failed(
                error,
                call,
                FlightSearchResult(
                    found=False, origin=origin, destination=destination, flights=[], error=FLIGHT_SEARCH_UNAVAILABLE
                ),
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

        try:
            booking = await workflow.execute_activity_method(
                ConferenceBookerActivities.book_flight,
                BookFlightInput(
                    flight_id=state.selected_flight.id,
                    traveller_name=state.traveller_details.traveller_name,
                    idempotency_key=_idempotency_key("flight", state.selected_flight.id),
                ),
                start_to_close_timeout=API_TIMEOUT,
                retry_policy=API_RETRY,
            )
        except ActivityError as error:
            if is_cancelled_exception(error):
                raise
            # The flight cannot be booked and retrying will not help. The conference ticket is
            # already paid for, so release it rather than leaving the attendee with half a journey.
            workflow.logger.error("Flight booking failed permanently (%s); compensating conference booking", error.cause)
            return await self._compensate_conference_booking()

        self._state = self._state.model_copy(update={"flight_booking": booking, "is_complete": True})
        return _succeeded(FlightBookingResult(booked=True, booking=booking, error=None))

    async def _compensate_conference_booking(self) -> tuple[str, str]:
        """Saga compensation. Releases the conference ticket after a flight booking that cannot
        succeed, and reports it so the model can tell the attendee what happened.

        The pattern itself is just try/except plus a compensating call. What Temporal adds is
        the guarantee that this runs even if the worker dies between the failed booking and the
        cancellation.
        """
        conference_booking = self._state.conference_booking
        if conference_booking is None:
            return _failed(
                FlightBookingResult(
                    booked=False, booking=None, error="The flight could not be booked. Nothing needed releasing."
                )
            )

        try:
            await workflow.execute_activity_method(
                ConferenceBookerActivities.cancel_conference_booking,
                conference_booking.booking_reference,
                start_to_close_timeout=API_TIMEOUT,
                retry_policy=API_RETRY,
            )
        except ActivityError as error:
            if is_cancelled_exception(error):
                raise
            # Compensation itself failed. Say so plainly rather than implying it worked.
            workflow.logger.error("Compensation failed: %s", error.cause)
            return _failed(
                FlightBookingResult(
                    booked=False,
                    booking=None,
                    error=(
                        f"The flight could not be booked and conference booking {conference_booking.booking_reference} "
                        "could not be cancelled automatically. Please contact the conference organiser."
                    ),
                )
            )

        self._state = self._state.model_copy(update={"conference_booking": None, "is_complete": False})
        return _failed(
            FlightBookingResult(
                booked=False,
                booking=None,
                error=(
                    f"The flight could not be booked, so conference booking {conference_booking.booking_reference} "
                    "has been cancelled. Nothing has been charged for this journey. Please start again."
                ),
            )
        )


def _idempotency_key(purpose: str, subject_id: str) -> str:
    """Stable across retries because it is derived from the workflow ID and what is being
    booked, and it changes if the attendee picks something else."""
    return f"{workflow.info().workflow_id}:{purpose}:{subject_id}"


def _api_failed(error: ActivityError, call: ModelToolCall, result: DomainModel) -> tuple[str, str]:
    """Turns a permanently failed API call into a tool result the model can explain.

    A retryable outage never reaches here: it keeps retrying until the API returns. What does
    reach here is a request that will fail identically for ever, and letting that escape would
    fail the whole turn instead of producing a reply.
    """
    workflow.logger.warning("Tool %s failed permanently (%s); reporting it to the model", call.name, error.cause)
    return _failed(result)


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
