"""Everything with a side effect: the model round-trip and the API calls.

Failures are classified, not caught. A retryable one (429, 5xx, network) propagates and
Temporal retries it with backoff, so an API outage mid-turn stalls the attendee's turn rather
than failing it. A permanent one (other 4xx) fails the activity immediately, and the workflow
decides what that means for the journey.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

from openai import AsyncOpenAI
from temporalio import activity

from conference_booker.agent.model import ModelResponse, invoke_model
from conference_booker.catalog import ConferenceBooking, ConferenceCatalogClient, ConferenceSummary
from conference_booker.flights import FlightBooking, FlightOption, FlightSearchClient
from conference_booker.models import CamelModel
from temporal_agent.failures import classify_api_failures, classify_model_failures

T = TypeVar("T")

CATALOG_API = "Conference Catalog API"
FLIGHTS_API = "Flight Search API"

# A model call can take tens of seconds. Heartbeating while it runs lets Temporal notice a dead
# worker within the workflow's heartbeat timeout, instead of waiting out start_to_close.
HEARTBEAT_INTERVAL_SECONDS = 1


class ModelRequest(CamelModel):
    messages: list[dict[str, Any]]
    required_tool: str | None


# The idempotency keys are derived from the workflow, so a retry after a POST that succeeded
# but timed out returns the original booking instead of creating a second one.


class BookConferenceInput(CamelModel):
    conference_id: str
    traveller_name: str
    idempotency_key: str


class SearchFlightsInput(CamelModel):
    origin: str
    destination: str


class BookFlightInput(CamelModel):
    flight_id: str
    traveller_name: str
    idempotency_key: str


class ConferenceBookerActivities:
    def __init__(
        self,
        openai: AsyncOpenAI,
        model: str,
        catalog: ConferenceCatalogClient,
        flights: FlightSearchClient,
    ) -> None:
        self._openai = openai
        self._model = model
        self._catalog = catalog
        self._flights = flights

    @activity.defn
    async def invoke_model(self, request: ModelRequest) -> ModelResponse:
        """One model round-trip. Returns the tool calls the model asked for instead of running
        them, so every tool call is the workflow's decision and appears in the event history."""
        return await _heartbeating(
            classify_model_failures(
                invoke_model(self._openai, self._model, request.messages, request.required_tool)
            )
        )

    @activity.defn
    async def get_available_conferences(self) -> list[ConferenceSummary]:
        return await classify_api_failures(CATALOG_API, self._catalog.get_available_conferences())

    @activity.defn
    async def book_conference(self, request: BookConferenceInput) -> ConferenceBooking:
        return await classify_api_failures(
            CATALOG_API,
            self._catalog.book_conference(request.conference_id, request.traveller_name, request.idempotency_key),
        )

    @activity.defn
    async def cancel_conference_booking(self, booking_reference: str) -> None:
        """Compensation: releases a conference ticket when the journey cannot complete."""
        return await classify_api_failures(CATALOG_API, self._catalog.cancel_booking(booking_reference))

    @activity.defn
    async def search_flights(self, request: SearchFlightsInput) -> list[FlightOption]:
        return await classify_api_failures(FLIGHTS_API, self._flights.search_flights(request.origin, request.destination))

    @activity.defn
    async def book_flight(self, request: BookFlightInput) -> FlightBooking:
        return await classify_api_failures(
            FLIGHTS_API,
            self._flights.book_flight(request.flight_id, request.traveller_name, request.idempotency_key),
        )


async def _heartbeating(work: Awaitable[T]) -> T:
    async def beat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    beater = asyncio.create_task(beat())
    try:
        return await work
    finally:
        beater.cancel()
