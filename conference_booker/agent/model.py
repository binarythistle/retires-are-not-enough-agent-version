"""One model round-trip. Messages are plain OpenAI Chat Completions dicts, which are already
serializable, so unlike the .NET version there are no mirror types to translate through.

The request carries exactly the fields the .NET agent sent (model, messages, tools,
tool_choice, reasoning_effort), so the WireMock LLM stubs match it.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

from conference_booker.agent.tools import DECLARATIONS
from conference_booker.journey import TokenUsage
from conference_booker.models import CamelModel

logger = logging.getLogger(__name__)

Message = dict[str, Any]


class ModelToolCall(CamelModel):
    """One tool call the model asked for."""

    call_id: str
    name: str
    arguments_json: str


class ModelResponse(CamelModel):
    """Either final text, or the tool calls the model wants run."""

    text: str | None
    tool_calls: list[ModelToolCall]
    usage: TokenUsage


def system_message(text: str) -> Message:
    return {"role": "system", "content": text}


def user_message(text: str) -> Message:
    return {"role": "user", "content": text}


def assistant_message(text: str) -> Message:
    return {"role": "assistant", "content": text}


def tool_calls_message(calls: list[ModelToolCall]) -> Message:
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": c.arguments_json}}
            for c in calls
        ],
    }


def tool_result_message(call_id: str, result_json: str) -> Message:
    return {"role": "tool", "tool_call_id": call_id, "content": result_json}


async def invoke_model(
    client: AsyncOpenAI,
    model: str,
    messages: list[Message],
    required_tool: str | None,
) -> ModelResponse:
    tool_choice: Any = (
        {"type": "function", "function": {"name": required_tool}} if required_tool else "auto"
    )

    completion = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=DECLARATIONS,
        tool_choice=tool_choice,
        reasoning_effort="none",
    )

    message = completion.choices[0].message
    tool_calls = [
        ModelToolCall(call_id=call.id, name=call.function.name, arguments_json=call.function.arguments or "{}")
        for call in (message.tool_calls or [])
        if call.type == "function"
    ]
    usage = (
        TokenUsage(
            input_tokens=completion.usage.prompt_tokens,
            output_tokens=completion.usage.completion_tokens,
            total_tokens=completion.usage.total_tokens,
        )
        if completion.usage
        else TokenUsage()
    )

    logger.info(
        "Model round-trip: %d in, %d out, %d tool call(s) requested",
        usage.input_tokens,
        usage.output_tokens,
        len(tool_calls),
    )

    return ModelResponse(text=(message.content or "").strip() or None, tool_calls=tool_calls, usage=usage)
