"""The journey as plain serializable values. The plain agent keeps it in memory; the Temporal
workflow will keep the same values in event history, so every field must be a plain payload.
"""

from __future__ import annotations

from conference_booker.catalog import ConferenceBooking, ConferenceSummary
from conference_booker.flights import FlightBooking, FlightOption
from conference_booker.models import CamelModel, DomainModel


class ConversationRoles:
    USER = "user"
    AGENT = "agent"


class ToolActivity(CamelModel):
    """One tool call as shown to the attendee."""

    name: str
    status: str
    duration_milliseconds: int


class ConversationEntry(CamelModel):
    """One line of the transcript, with the tool calls made while producing it."""

    role: str
    message: str
    tool_activities: list[ToolActivity] = []


class TravellerDetails(DomainModel):
    traveller_name: str
    departure_location: str
    travel_preference: str


class TokenUsage(CamelModel):
    """Tokens billed for model work. A provider that reports no usage contributes zero."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class JourneyState(CamelModel):
    """The whole journey. Immutable: every change produces a new state via model_copy."""

    entries: list[ConversationEntry] = []
    available_conferences: list[ConferenceSummary] = []
    selected_conference: ConferenceSummary | None = None
    conference_booking: ConferenceBooking | None = None
    traveller_details: TravellerDetails | None = None
    available_flights: list[FlightOption] = []
    selected_flight: FlightOption | None = None
    flight_booking: FlightBooking | None = None
    is_complete: bool = False
    tokens_used: TokenUsage = TokenUsage()

    def record(
        self,
        user_message: str,
        agent_message: str,
        record_user_message: bool,
        tool_activities: list[ToolActivity],
    ) -> JourneyState:
        entries = list(self.entries)
        if record_user_message:
            entries.append(ConversationEntry(role=ConversationRoles.USER, message=user_message))
        entries.append(
            ConversationEntry(role=ConversationRoles.AGENT, message=agent_message, tool_activities=tool_activities)
        )
        return self.model_copy(update={"entries": entries})


class AgentTurnReply(CamelModel):
    message: str
    tool_activities: list[ToolActivity]
    is_complete: bool


WELCOME_PROMPT = "Open the conversation and welcome the attendee."
ALREADY_COMPLETE = "This journey is already complete. Start a new journey to make another booking."


class ConversationTranscript(CamelModel):
    """Whole transcript, used to restore the page after a refresh."""

    messages: list[ConversationEntry]
    is_complete: bool
