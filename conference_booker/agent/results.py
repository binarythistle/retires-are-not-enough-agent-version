"""The shapes the model sees as tool results. PascalCase on the wire, field for field what the
.NET agent sent, so the model's behaviour and the WireMock stubs carry over unchanged.
"""

from __future__ import annotations

from conference_booker.catalog import ConferenceBooking, ConferenceSummary
from conference_booker.flights import FlightBooking, FlightOption
from conference_booker.journey import TravellerDetails
from conference_booker.models import DomainModel


class CatalogueToolResult(DomainModel):
    conferences: list[ConferenceSummary]
    error: str | None


class ConferenceBookingResult(DomainModel):
    booked: bool
    booking: ConferenceBooking | None
    error: str | None


class FlightSearchResult(DomainModel):
    found: bool
    origin: str | None
    destination: str | None
    flights: list[FlightOption]
    error: str | None


class FlightBookingResult(DomainModel):
    booked: bool
    booking: FlightBooking | None
    error: str | None


class ConferenceSelectionResult(DomainModel):
    selected: bool
    conference: ConferenceSummary | None
    error: str | None


class FlightSelectionResult(DomainModel):
    selected: bool
    flight: FlightOption | None
    error: str | None


class TravellerDetailsResult(DomainModel):
    saved: bool
    details: TravellerDetails | None
    errors: list[str]
