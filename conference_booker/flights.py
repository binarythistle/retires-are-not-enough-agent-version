"""Flight records and the HTTP boundary to the Flight Search API."""

from __future__ import annotations

from datetime import datetime, time

import httpx

from conference_booker.models import DomainModel, Money


class FlightOption(DomainModel):
    id: str
    airline: str
    flight_number: str
    origin_city: str
    origin_airport: str
    destination_city: str
    destination_airport: str
    departure_time: time
    arrival_time: time
    arrival_day_offset: int
    price: Money
    currency: str


class FlightBooking(DomainModel):
    booking_reference: str
    status: str
    booked_at: datetime
    traveller_name: str
    flight: FlightOption


class FlightSearchClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def search_flights(self, origin: str, destination: str) -> list[FlightOption]:
        response = await self._http.get("/flights", params={"origin": origin, "destination": destination})
        response.raise_for_status()
        return [FlightOption.model_validate(item) for item in response.json()]

    async def book_flight(self, flight_id: str, traveller_name: str, idempotency_key: str | None = None) -> FlightBooking:
        response = await self._http.post(
            "/bookings",
            json={"flightId": flight_id, "travellerName": traveller_name, "idempotencyKey": idempotency_key},
        )
        response.raise_for_status()
        return FlightBooking.model_validate(response.json())
