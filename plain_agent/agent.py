"""Slice 2 stand-in for the agent: echoes the attendee so the web shell and UI can be tested
without a model. Slice 3 replaces it with the real agent loop behind the same two methods.
"""

from __future__ import annotations

import time

from conference_booker.journey import ALREADY_COMPLETE, WELCOME_PROMPT, AgentTurnReply, ToolActivity
from plain_agent.store import Conversation


class EchoAgent:
    async def start(self, conversation: Conversation) -> AgentTurnReply:
        return await self._run(conversation, WELCOME_PROMPT, record_user_message=False)

    async def run(self, conversation: Conversation, user_message: str) -> AgentTurnReply:
        return await self._run(conversation, user_message, record_user_message=True)

    async def _run(self, conversation: Conversation, user_message: str, record_user_message: bool) -> AgentTurnReply:
        async with conversation.gate:
            state = conversation.state

            if state.is_complete:
                conversation.state = state.record(user_message, ALREADY_COMPLETE, record_user_message, [])
                return AgentTurnReply(message=ALREADY_COMPLETE, tool_activities=[], is_complete=True)

            started = time.perf_counter()
            tool_activities: list[ToolActivity] = []

            if not record_user_message:
                message = "Welcome to Conference Booker (echo mode). Type anything and I will repeat it."
            else:
                message = f"You said: {user_message}"
                # A fake tool call so the activity chips render.
                tool_activities.append(
                    ToolActivity(
                        name="echo_message",
                        status="succeeded",
                        duration_milliseconds=round((time.perf_counter() - started) * 1000),
                    )
                )

            # Typing "complete" exercises the journey-complete UI and the restart button.
            if user_message.strip().lower() == "complete":
                state = state.model_copy(update={"is_complete": True})
                message = "Journey complete\n\nThis echo journey is complete."

            conversation.state = state.record(user_message, message, record_user_message, tool_activities)
            return AgentTurnReply(message=message, tool_activities=tool_activities, is_complete=state.is_complete)
