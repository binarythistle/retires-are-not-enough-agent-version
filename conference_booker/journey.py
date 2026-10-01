"""The journey as plain serializable values. The plain agent keeps it in memory; the Temporal
workflow will keep the same values in event history, so every field must be a plain payload.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """camelCase on the wire, matching what site.js expects from the .NET app."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


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


class JourneyState(CamelModel):
    """The whole journey. Booking state is added alongside the transcript in later slices."""

    entries: list[ConversationEntry] = []
    is_complete: bool = False

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
