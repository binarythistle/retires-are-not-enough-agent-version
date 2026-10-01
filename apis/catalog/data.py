"""Static demo data. A later iteration may replace this source with a database or upstream service."""

from __future__ import annotations

from datetime import date

from apis.models import ApiModel


class Conference(ApiModel):
    id: str
    name: str
    location: str
    starts_on: date
    ends_on: date
    description: str


CONFERENCES: list[Conference] = [
    Conference(
        id="aicontext-hamburg-2027",
        name="AI Context",
        location="Hamburg, Germany",
        starts_on=date(2027, 9, 2),
        ends_on=date(2027, 9, 4),
        description="Bringing Container Days and APIs together",
    ),
    Conference(
        id="world-ai-summit-amsterdam-2027",
        name="World AI Summit",
        location="Amsterdam, Netherlands",
        starts_on=date(2027, 10, 7),
        ends_on=date(2027, 10, 8),
        description="Expore a world of AI",
    ),
    Conference(
        id="dotnet-london-2028",
        name=".NET Conference London",
        location="London, United Kingdom",
        starts_on=date(2028, 2, 18),
        ends_on=date(2028, 2, 19),
        description="Two days of .NET, cloud, and AI engineering sessions.",
    ),
    Conference(
        id="ai-dev-berlin-2028",
        name="AI Developer Summit",
        location="Berlin, Germany",
        starts_on=date(2028, 4, 14),
        ends_on=date(2028, 4, 16),
        description="A practical conference for teams building AI-enabled products.",
    ),
    Conference(
        id="software-craft-manchester-2028",
        name="Software Craft Manchester",
        location="Manchester, United Kingdom",
        starts_on=date(2028, 6, 9),
        ends_on=date(2028, 6, 10),
        description="Hands-on sessions covering architecture, delivery, and engineering practice.",
    ),
    Conference(
        id="cloud-native-amsterdam-2028",
        name="Cloud Native Exchange",
        location="Amsterdam, Netherlands",
        starts_on=date(2028, 9, 22),
        ends_on=date(2028, 9, 23),
        description="A focused event for platform engineering, containers, and cloud operations.",
    ),
]


def find_conference(conference_id: str) -> Conference | None:
    return next((c for c in CONFERENCES if c.id.lower() == conference_id.lower()), None)
