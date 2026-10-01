"""Ephemeral conversation store. Everything here lives in this process's memory, so stopping
the process loses every conversation. That is the point of the plain version.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from conference_booker.journey import JourneyState


@dataclass
class Conversation:
    state: JourneyState = field(default_factory=JourneyState)
    # One turn at a time per conversation, so two quick submits cannot race on the same state.
    gate: asyncio.Lock = field(default_factory=asyncio.Lock)


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[uuid.UUID, Conversation] = {}

    def get_or_create(self, conversation_id: uuid.UUID) -> Conversation:
        return self._conversations.setdefault(conversation_id, Conversation())

    def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def remove(self, conversation_id: uuid.UUID) -> None:
        self._conversations.pop(conversation_id, None)
