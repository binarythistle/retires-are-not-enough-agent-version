"""What the browser and the web apps exchange. Shared by the plain and Temporal apps so the
same site.js works against either.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.responses import JSONResponse

from conference_booker.journey import AgentTurnReply, ConversationEntry, ToolActivity
from conference_booker.models import CamelModel

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


# Start chat: no message, just used to open the conversation.
class ConversationRequest(CamelModel):
    conversation_id: str | None = None


# Continue chat: message required. Optional here so a missing one is our 400, not a 422.
class ChatRequest(CamelModel):
    message: str | None = None
    conversation_id: str | None = None


class ChatReply(CamelModel):
    message: str
    conversation_id: uuid.UUID
    tool_activities: list[ToolActivity]
    is_complete: bool

    @classmethod
    def of(cls, conversation_id: uuid.UUID, reply: AgentTurnReply) -> ChatReply:
        return cls(
            message=reply.message,
            conversation_id=conversation_id,
            tool_activities=reply.tool_activities,
            is_complete=reply.is_complete,
        )


# Carries the whole conversation history.
class ConversationTranscriptReply(CamelModel):
    conversation_id: uuid.UUID
    messages: list[ConversationEntry]
    is_complete: bool


def parse_id(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)
