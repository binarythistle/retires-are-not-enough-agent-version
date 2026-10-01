"""Conference records and the HTTP boundary to the Conference Catalog API."""

from __future__ import annotations

from datetime import date, datetime

import httpx

from conference_booker.models import DomainModel


class ConferenceSummary(DomainModel):
    id: str
    name: str
    location: str
    starts_on: date
    ends_on: date
    description: str


class ConferenceBooking(DomainModel):
    booking_reference: str
    status: str
    booked_at: datetime
    traveller_name: str
    conference: ConferenceSummary


class ConferenceCatalogClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_available_conferences(self) -> list[ConferenceSummary]:
        response = await self._http.get("/conferences")
        response.raise_for_status()
        return [ConferenceSummary.model_validate(item) for item in response.json()]
