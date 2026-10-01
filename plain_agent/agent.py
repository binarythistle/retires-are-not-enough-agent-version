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
from conference_booker.agent.results import CatalogueToolResult
from conference_booker.catalog import ConferenceCatalogClient
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

# Declared to the model but not built yet. Slice 4 implements them.
NOT_YET_BUILT = {
    tools.SELECT_CONFERENCE,
    tools.BOOK_SELECTED_CONFERENCE,
    tools.GET_FLIGHT_OPTIONS,
    tools.SELECT_FLIGHT,
    tools.BOOK_SELECTED_FLIGHT,
}


class ConferenceBookerAgent:
    def __init__(self, openai: AsyncOpenAI, model: str, catalog: ConferenceCatalogClient) -> None:
        self._openai = openai
        self._model = model
        self._catalog = catalog

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

            case name if name in NOT_YET_BUILT:
                return json.dumps({"error": f"'{name}' is not available yet. Tell the attendee this step is coming soon."}), "failed"

            case _:
                return json.dumps({"error": f"Unknown tool '{call.name}'."}), "failed"


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
