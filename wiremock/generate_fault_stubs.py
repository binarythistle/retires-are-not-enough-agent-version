"""Generates retryable fault stubs: calls that fail a few times with 503, 429, 500 or a dropped
connection, then recover. They exercise Temporal's retry policies without leaving the happy
path, so the happy path stubs still answer once the fault clears. Run from the repo root:

    uv run python wiremock/generate_fault_stubs.py

Writes:
- catalog-booking-fault-stubs.json  import into the Catalog mock, alongside catalog-stubs.json
- llm-fault-stubs.json              import into the LLM mock, alongside llm-happy-path-stubs.json

Each fault is a WireMock scenario: every failing call moves it on one state, and once it
reaches "Recovered" the request falls through to the normal stub. Here, unlike the happy path
stubs, a scenario is exactly what is wanted, because each Temporal retry should see the next
failure. Reset scenarios in WireMock Cloud to arm the faults again.

Every failure here is one the agent classifies as retryable (429, 5xx, network), so the
Temporal version retries with backoff and the attendee just sees a slow reply. The plain
version has no such retries: the catalog failure becomes a failed tool result immediately.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_HEADERS = {"Content-Type": "application/json"}

# What each failing attempt returns. "fault" drops the connection instead of answering.
HTTP_503 = {"status": 503, "headers": {**JSON_HEADERS, "Retry-After": "1"}}
HTTP_429 = {"status": 429, "headers": {**JSON_HEADERS, "Retry-After": "1"}}
HTTP_500 = {"status": 500, "headers": JSON_HEADERS}
CONNECTION_RESET = {"fault": "CONNECTION_RESET_BY_PEER"}


def failing_sequence(scenario: str, request: dict, failures: list[tuple[dict, dict | None]]) -> list[dict]:
    """One stub per failure, chained through scenario states; then the scenario is Recovered
    and the request falls through to the lower-priority normal stub."""
    stubs = []
    for n, (response, body) in enumerate(failures, start=1):
        label = response.get("fault") or f"HTTP {response['status']}"
        stub_response = dict(response)
        if body is not None:
            stub_response["jsonBody"] = body
        stubs.append(
            {
                "name": f"{scenario} {n}/{len(failures)} ({label})",
                "priority": 1,
                "scenarioName": scenario,
                "requiredScenarioState": "Started" if n == 1 else f"Failed {n - 1}",
                "newScenarioState": "Recovered" if n == len(failures) else f"Failed {n}",
                "request": request,
                "response": stub_response,
            }
        )
    return stubs


def api_error(message: str) -> dict:
    return {"error": f"Simulated fault: {message}", "injected": True}


def openai_error(message: str, error_type: str, code: str | None = None) -> dict:
    return {"error": {"message": f"(mock) {message}", "type": error_type, "param": None, "code": code}}


def catalog_booking_faults() -> dict:
    request = {"method": "POST", "urlPath": "/bookings"}
    return {
        "mappings": failing_sequence(
            "Conference booking flaky",
            request,
            [
                (HTTP_503, api_error("the Catalog API is unavailable.")),
                (HTTP_429, api_error("too many requests.")),
                (CONNECTION_RESET, None),
            ],
        )
    }


def llm_faults() -> dict:
    def forced(tool: str) -> dict:
        return {
            "method": "POST",
            "urlPath": "/chat/completions",
            "bodyPatterns": [{"matchesJsonPath": {"expression": "$.tool_choice.function.name", "equalTo": tool}}],
        }

    return {
        "mappings": [
            *failing_sequence(
                "LLM flaky on book conference",
                forced("book_selected_conference"),
                [
                    (HTTP_503, openai_error("The server is overloaded. Please try again.", "server_error")),
                    (HTTP_429, openai_error("Rate limit reached. Please try again.", "requests", "rate_limit_exceeded")),
                ],
            ),
            *failing_sequence(
                "LLM flaky on book flight",
                forced("book_selected_flight"),
                [
                    (HTTP_500, openai_error("The server had an error processing your request.", "server_error")),
                    (CONNECTION_RESET, None),
                ],
            ),
        ]
    }


def write(name: str, stubs: dict) -> None:
    (HERE / name).write_text(json.dumps(stubs, indent=2, ensure_ascii=False) + "\n")
    print(f"{name}: {len(stubs['mappings'])} mappings")
    for m in stubs["mappings"]:
        print(f"  {m['name']}")


if __name__ == "__main__":
    write("catalog-booking-fault-stubs.json", catalog_booking_faults())
    write("llm-fault-stubs.json", llm_faults())
