"""Conference Catalog API. Run with:

    uv run uvicorn apis.catalog.main:app --port 5056
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from apis.catalog.data import CONFERENCES, Conference, find_conference
from apis.faults import use_fault_injection
from apis.models import ApiModel, booking_reference, error


class ConferenceBookingRequest(ApiModel):
    # Optional so a missing field is our 400, not FastAPI's 422, matching the .NET API.
    conference_id: str | None = None
    traveller_name: str | None = None
    # Lets a caller retry safely: the same key always yields the same booking.
    idempotency_key: str | None = None


class ConferenceBookingConfirmation(ApiModel):
    booking_reference: str
    status: str
    booked_at: datetime
    traveller_name: str
    conference: Conference


app = FastAPI(title="Conference Catalog API")

# The demo control panel can make this API misbehave without killing the process.
use_fault_injection(app)

# Keys are uppercased booking references, so lookups are case-insensitive.
bookings: dict[str, ConferenceBookingConfirmation] = {}
# Lowercased idempotency key -> booking reference, so a retried POST returns the original booking.
booking_keys: dict[str, str] = {}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/conferences")
def list_conferences() -> list[Conference]:
    return CONFERENCES


@app.get("/conferences/{conference_id}")
def get_conference(conference_id: str):
    conference = find_conference(conference_id)
    return conference if conference else error(404, f"Conference '{conference_id}' was not found.")


@app.post("/bookings")
def create_booking(request: ConferenceBookingRequest):
    if not (request.conference_id or "").strip() or not (request.traveller_name or "").strip():
        return error(400, "conferenceId and travellerName are required.")

    conference = find_conference(request.conference_id)
    if conference is None:
        return error(404, f"Conference '{request.conference_id}' was not found.")

    key = (request.idempotency_key or "").strip().lower()
    if key and (existing := bookings.get(booking_keys.get(key, ""))):
        # Already booked under this key. Return the original rather than booking twice.
        return existing

    confirmation = ConferenceBookingConfirmation(
        booking_reference=booking_reference("CONF-", 13),
        status="Confirmed",
        booked_at=datetime.now(UTC),
        traveller_name=request.traveller_name.strip(),
        conference=conference,
    )
    bookings[confirmation.booking_reference] = confirmation

    if key:
        booking_keys[key] = confirmation.booking_reference

    return JSONResponse(
        confirmation.model_dump(mode="json", by_alias=True),
        status_code=201,
        headers={"Location": f"/bookings/{confirmation.booking_reference}"},
    )


# Compensation for the booking saga. Idempotent: cancelling twice is still cancelled.
@app.post("/bookings/{reference}/cancel")
def cancel_booking(reference: str):
    booking = bookings.get(reference.upper())
    if booking is None:
        return error(404, f"Booking '{reference}' was not found.")

    cancelled = booking.model_copy(update={"status": "Cancelled"})
    bookings[cancelled.booking_reference] = cancelled
    return cancelled


@app.get("/bookings")
def list_bookings() -> list[ConferenceBookingConfirmation]:
    return sorted(bookings.values(), key=lambda b: b.booked_at, reverse=True)


@app.get("/bookings/{reference}")
def get_booking(reference: str):
    booking = bookings.get(reference.upper())
    return booking if booking else error(404, f"Booking '{reference}' was not found.")
