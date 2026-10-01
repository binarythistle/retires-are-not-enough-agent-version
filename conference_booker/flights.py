"""Flight records. The HTTP boundary to the Flight Search API arrives with the flight tools."""

from __future__ import annotations

from datetime import datetime, time

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
