"""Generates the WireMock Cloud stubs for the mocked Conference Catalog API from the same data
the self-hosted API serves, so the mock cannot drift from it. Run from the repo root:

    uv run python wiremock/generate_catalog_stubs.py

Writes:
- catalog-stubs.json         list, bookings for every conference, cancel, unknown-conference 404
- catalog-outage-stubs.json  five 503s on GET /conferences, then recovery (a WireMock scenario)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # so the API's data imports when run as a script

from apis.catalog.data import CONFERENCES  # noqa: E402
JSON_HEADERS = {"Content-Type": "application/json"}
OUTAGE_FAILURES = 5


def conference_json(conference) -> dict:
    return conference.model_dump(mode="json", by_alias=True)


def booking_stub(conference) -> dict:
    # Templated so each booking gets a fresh reference and echoes the traveller, as the real
    # API does. The reference format matches the API: CONF- plus 8 uppercase hex characters.
    body = {
        "bookingReference": "CONF-{{randomValue length=8 type='HEXADECIMAL' uppercase=true}}",
        "status": "Confirmed",
        "bookedAt": "{{now}}",
        "travellerName": "{{jsonPath request.body '$.travellerName'}}",
        "conference": conference_json(conference),
    }
    return {
        "name": f"Book {conference.name}",
        "priority": 5,
        "request": {
            "method": "POST",
            "urlPath": "/bookings",
            "bodyPatterns": [{"matchesJsonPath": f"$[?(@.conferenceId == '{conference.id}')]"}],
        },
        "response": {
            "status": 201,
            "headers": JSON_HEADERS,
            "transformers": ["response-template"],
            "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        },
    }


def catalog_stubs() -> dict:
    mappings = [
        {
            "name": "List conferences",
            "priority": 5,
            "request": {"method": "GET", "url": "/conferences"},
            "response": {"status": 200, "headers": JSON_HEADERS, "jsonBody": [conference_json(c) for c in CONFERENCES]},
        },
        *(booking_stub(c) for c in CONFERENCES),
        {
            "name": "Book unknown conference (404)",
            "priority": 10,
            "request": {"method": "POST", "urlPath": "/bookings"},
            "response": {
                "status": 404,
                "headers": JSON_HEADERS,
                "transformers": ["response-template"],
                "body": "{\"error\":\"Conference '{{jsonPath request.body '$.conferenceId'}}' was not found.\"}",
            },
        },
        {
            # The agent only checks for success here, so a bare status is enough.
            "name": "Cancel conference booking",
            "priority": 5,
            "request": {"method": "POST", "urlPathTemplate": "/bookings/{bookingReference}/cancel"},
            "response": {"status": 200, "headers": JSON_HEADERS, "jsonBody": {"status": "Cancelled"}},
        },
    ]
    return {"mappings": mappings}


def outage_stubs() -> dict:
    # A WireMock scenario: each GET /conferences moves it on one state, so the first five fail
    # and later calls fall through to the normal list stub. Reset the scenario to replay it.
    mappings = []
    for n in range(1, OUTAGE_FAILURES + 1):
        mappings.append(
            {
                "name": f"List conferences outage {n}/{OUTAGE_FAILURES} (503)",
                "priority": 1,
                "scenarioName": "Catalogue outage",
                "requiredScenarioState": "Started" if n == 1 else f"Failed {n - 1}",
                "newScenarioState": "Recovered" if n == OUTAGE_FAILURES else f"Failed {n}",
                "request": {"method": "GET", "url": "/conferences"},
                "response": {
                    "status": 503,
                    "headers": JSON_HEADERS,
                    "jsonBody": {"error": f"Simulated outage, failure {n} of {OUTAGE_FAILURES}."},
                },
            }
        )
    return {"mappings": mappings}


def write(name: str, stubs: dict) -> None:
    (HERE / name).write_text(json.dumps(stubs, indent=2, ensure_ascii=False) + "\n")
    print(f"{name}: {len(stubs['mappings'])} mappings")


if __name__ == "__main__":
    write("catalog-stubs.json", catalog_stubs())
    write("catalog-outage-stubs.json", outage_stubs())
