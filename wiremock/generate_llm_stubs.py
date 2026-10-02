"""Turns one recorded conversation into llm-happy-path-stubs.json for the WireMock LLM mock.

Record a run first (see wiremock/README.md), then from the repo root:

    uv run python wiremock/generate_llm_stubs.py              # newest run in wiremock/recordings
    uv run python wiremock/generate_llm_stubs.py 20261001-153000

Unlike the .NET generator, nothing here is tied to one recording's step numbers: how each
stub matches is derived from what the recorded request looked like. Each request carries the
whole transcript, so stubs match on how the request ends rather than the full body:

| Recorded request                       | Stub matches on                                         |
| -------------------------------------- | ------------------------------------------------------- |
| The agent forced a tool                | $.tool_choice.function.name                             |
| Ends with a tool result                | $.messages[-1].tool_call_id (the recorded call's ID)    |
| Ends with a "yes"-ish attendee message | that, and the agent's question in $.messages[-2]        |
| Ends with any other attendee message   | $.messages[-1].content containing it, ignoring case     |

A forced booking also gets a "yes" twin, because "yes" is not an explicit booking instruction
so the agent does not force the tool, and the model would otherwise go off script.

Booking references come from the booking APIs, so replies that quote one copy it from the
live tool result with a response template rather than replaying the recorded one.

WireMock scenarios are deliberately not used: a Temporal retry or a worker restart would
advance the scenario and desync the conversation. The stubs keep no state.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORDINGS = HERE / "recordings"

# What a stub accepts as "yes": generous, so "ok, yes please" works on stage.
YES = r"(?i).*\b(yes|yeah|yep|sure|please|ok|okay)\b.*"
# What counts as a recorded "yes" turn: strict, so "Please list Amsterdam" is not one.
RECORDED_YES = re.compile(r"(?i)\s*(yes|yeah|yep|sure|ok|okay)\b[\s\w,.!]{0,20}")
BOOKING_TOOLS = ("book_selected_conference", "book_selected_flight")
BOOKING_REFERENCE = re.compile(r"\b(?:CONF|CB)-[0-9A-F]{8}\b")
REF_TEMPLATE = "{{jsonPath (jsonPath request.body '$.messages[-1].content') '$.Booking.BookingReference'}}"


def last(field: str, **match) -> dict:
    return {"matchesJsonPath": {"expression": f"$.messages[-1].{field}", **match}}


def user_says(text: str) -> list[dict]:
    # Contains rather than equals, so extra words around the scripted text still match.
    return [last("role", equalTo="user"), last("content", matches=rf"(?is).*{re.escape(text.strip())}.*")]


def tool_result(call_id: str) -> list[dict]:
    return [last("role", equalTo="tool"), last("tool_call_id", equalTo=call_id)]


def forced(tool: str) -> list[dict]:
    return [{"matchesJsonPath": {"expression": "$.tool_choice.function.name", "equalTo": tool}}]


def yes_to(question: str) -> list[dict]:
    """A 'yes' answering this question. Matches on the question's last line, with any booking
    reference wildcarded because replayed replies quote a live one."""
    line = [ln for ln in question.strip().splitlines() if ln.strip()][-1].strip()
    pattern = r"[A-Z]+-[0-9A-F]{8}".join(re.escape(part) for part in BOOKING_REFERENCE.split(line))
    return [
        last("role", equalTo="user"),
        last("content", matches=YES),
        {"matchesJsonPath": {"expression": "$.messages[-2].content", "matches": rf"(?is).*{pattern}\s*"}},
    ]


def describe(response_message: dict) -> str:
    if calls := response_message.get("tool_calls"):
        return " + ".join(c["function"]["name"] for c in calls)
    return "reply: " + (response_message.get("content") or "").strip().splitlines()[0][:50]


def stub(name: str, patterns: list[dict], recorded_request: dict, response_body: dict) -> dict:
    response: dict = {"status": 200, "headers": {"Content-Type": "application/json"}, "jsonBody": response_body}

    # A reply straight after a booking quotes its reference: copy it from the live tool result.
    final = recorded_request["messages"][-1]
    message = response_body["choices"][0]["message"]
    if final["role"] == "tool" and message.get("content"):
        try:
            recorded_ref = json.loads(final["content"])["Booking"]["BookingReference"]
        except (ValueError, KeyError, TypeError):
            recorded_ref = None
        if recorded_ref and recorded_ref in message["content"]:
            message["content"] = message["content"].replace(recorded_ref, REF_TEMPLATE)
            response["transformers"] = ["response-template"]

    return {
        "name": name,
        "priority": 5,
        "request": {"method": "POST", "urlPath": "/chat/completions", "bodyPatterns": patterns},
        "response": response,
    }


def generate(run_prefix: str) -> dict:
    files = sorted(RECORDINGS.glob(f"{run_prefix}-*.json"))
    if not files:
        sys.exit(f"No recordings matching {RECORDINGS}/{run_prefix}-*.json")

    mappings: list[dict] = []
    seen: set[str] = set()

    def add(name: str, patterns: list[dict], request: dict, response: dict) -> None:
        # A Temporal retry records the same exchange twice; keep the first.
        key = json.dumps(patterns, sort_keys=True)
        if key in seen:
            print(f"  skipped duplicate: {name}")
            return
        seen.add(key)
        mappings.append(stub(name, patterns, request, json.loads(json.dumps(response))))

    for number, path in enumerate(files, start=1):
        exchange = json.loads(path.read_text())
        if exchange["status"] != 200:
            print(f"  skipped {path.name}: HTTP {exchange['status']}")
            continue

        request, response = exchange["request"], exchange["response"]
        final = request["messages"][-1]
        outcome = describe(response["choices"][0]["message"])
        tool_choice = request.get("tool_choice")

        if isinstance(tool_choice, dict):
            tool = tool_choice["function"]["name"]
            add(f"LLM {number:02d}: forced {tool} -> {outcome}", forced(tool), request, response)
            if tool in BOOKING_TOOLS and len(request["messages"]) >= 2:
                question = request["messages"][-2].get("content") or ""
                if question:
                    add(f"LLM {number:02d}: yes to booking -> {outcome}", yes_to(question), request, response)
        elif final["role"] == "tool":
            add(f"LLM {number:02d}: tool result -> {outcome}", tool_result(final["tool_call_id"]), request, response)
        elif RECORDED_YES.fullmatch(final["content"]) and request["messages"][-2].get("content"):
            add(f"LLM {number:02d}: yes -> {outcome}", yes_to(request["messages"][-2]["content"]), request, response)
        else:
            said = final["content"].strip()
            add(f"LLM {number:02d}: '{said[:40]}' -> {outcome}", user_says(said), request, response)

    mappings.append(
        {
            "name": "LLM fallback: off the happy-path script",
            "priority": 100,
            "request": {"method": "POST", "urlPath": "/chat/completions"},
            "response": {
                "status": 200,
                "headers": {"Content-Type": "application/json"},
                "jsonBody": {
                    "id": "chatcmpl-mock-fallback",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "mock",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "refusal": None,
                                "content": "(mock) That message is not in the recorded happy path, so I have no scripted reply for it.",
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            },
        }
    )
    return {"mappings": mappings}


def newest_run_prefix() -> str:
    prefixes = sorted({p.name.rsplit("-", 1)[0] for p in RECORDINGS.glob("*-*-*.json")})
    if not prefixes:
        sys.exit(f"No recordings in {RECORDINGS}. Record a run first (see wiremock/README.md).")
    return prefixes[-1]


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else newest_run_prefix()
    stubs = generate(prefix)
    for m in stubs["mappings"]:
        print(f"  {m['name']}")
    (HERE / "llm-happy-path-stubs.json").write_text(json.dumps(stubs, indent=2, ensure_ascii=False) + "\n")
    print(f"llm-happy-path-stubs.json: {len(stubs['mappings'])} mappings from run {prefix}")
