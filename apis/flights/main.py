"""Flight Search API. Run with:

    uv run uvicorn apis.flights.main:app --port 5057
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from apis.faults import use_fault_injection
from apis.flights.data import FLIGHTS, Flight, find_flight
from apis.models import ApiModel, booking_reference, error


class FlightBookingRequest(ApiModel):
    # Optional so a missing field is our 400, not FastAPI's 422, matching the .NET API.
    flight_id: str | None = None
    traveller_name: str | None = None
    # Lets a caller retry safely: the same key always yields the same booking.
    idempotency_key: str | None = None


class FlightBookingConfirmation(ApiModel):
    booking_reference: str
    status: str
    booked_at: datetime
    traveller_name: str
    flight: Flight


app = FastAPI(title="Flight Search API")

# The demo control panel can make this API misbehave without killing the process.
use_fault_injection(app)

# Keys are uppercased booking references, so lookups are case-insensitive.
bookings: dict[str, FlightBookingConfirmation] = {}
# Lowercased idempotency key -> booking reference, so a retried POST returns the original booking.
booking_keys: dict[str, str] = {}


def matches_location(city: str, airport: str, search_term: str | None) -> bool:
    if not search_term or not search_term.strip():
        return True
    term = search_term.strip().lower()
    return term in (city.lower(), airport.lower())


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/flights")
def search_flights(origin: str | None = None, destination: str | None = None) -> list[Flight]:
    return [
        flight
        for flight in FLIGHTS
        if matches_location(flight.origin_city, flight.origin_airport, origin)
        and matches_location(flight.destination_city, flight.destination_airport, destination)
    ]


@app.get("/flights/{flight_id}")
def get_flight(flight_id: str):
    flight = find_flight(flight_id)
    return flight if flight else error(404, f"Flight '{flight_id}' was not found.")


@app.post("/bookings")
def create_booking(request: FlightBookingRequest):
    if not (request.flight_id or "").strip() or not (request.traveller_name or "").strip():
        return error(400, "flightId and travellerName are required.")

    flight = find_flight(request.flight_id)
    if flight is None:
        return error(404, f"Flight '{request.flight_id}' was not found.")

    key = (request.idempotency_key or "").strip().lower()
    if key and (existing := bookings.get(booking_keys.get(key, ""))):
        # Already booked under this key. Return the original rather than booking twice.
        return existing

    confirmation = FlightBookingConfirmation(
        booking_reference=booking_reference("CB-", 11),
        status="Confirmed",
        booked_at=datetime.now(UTC),
        traveller_name=request.traveller_name.strip(),
        flight=flight,
    )
    bookings[confirmation.booking_reference] = confirmation

    if key:
        booking_keys[key] = confirmation.booking_reference

    return JSONResponse(
        confirmation.model_dump(mode="json", by_alias=True),
        status_code=201,
        headers={"Location": f"/bookings/{confirmation.booking_reference}"},
    )


@app.get("/bookings")
def list_bookings() -> list[FlightBookingConfirmation]:
    return sorted(bookings.values(), key=lambda b: b.booked_at, reverse=True)


@app.get("/bookings/{reference}")
def get_booking(reference: str):
    booking = bookings.get(reference.upper())
    return booking if booking else error(404, f"Booking '{reference}' was not found.")
