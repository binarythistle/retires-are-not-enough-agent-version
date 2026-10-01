"""Static daily flight schedules. Every Conference Booker destination city has incoming coverage."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

from apis.models import ApiModel


def _json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


# A JSON number written as .NET writes decimal (115, not "115" or 115.0).
Money = Annotated[Decimal, PlainSerializer(_json_number, return_type=int | float, when_used="json")]


class Flight(ApiModel):
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


def _flight(
    id: str, airline: str, number: str,
    origin_city: str, origin_airport: str, destination_city: str, destination_airport: str,
    departs: time, arrives: time, day_offset: int, price: int, currency: str,
) -> Flight:
    return Flight(
        id=id, airline=airline, flight_number=number,
        origin_city=origin_city, origin_airport=origin_airport,
        destination_city=destination_city, destination_airport=destination_airport,
        departure_time=departs, arrival_time=arrives, arrival_day_offset=day_offset,
        price=Decimal(price), currency=currency,
    )


FLIGHTS: list[Flight] = [
    _flight("ber-lon-eurowings-ew421", "Eurowings", "EW 421", "Berlin", "BER", "London", "LHR",
            time(8, 20), time(9, 15), 0, 149, "GBP"),
    _flight("ams-lon-british-airways-ba118", "British Airways", "BA 118", "Amsterdam", "AMS", "London", "LCY",
            time(9, 10), time(9, 20), 0, 254, "GBP"),
    _flight("ams-lon-klm-kl218", "KLM", "KL 218", "Amsterdam", "AMS", "London", "LCY",
            time(9, 10), time(9, 20), 0, 119, "GBP"),
    _flight("lon-ber-eurowings-ew422", "Eurowings", "EW 422", "London", "LHR", "Berlin", "BER",
            time(8, 5), time(10, 55), 0, 159, "GBP"),
    _flight("man-ber-easyjet-u2206", "easyJet", "U2 206", "Manchester", "MAN", "Berlin", "BER",
            time(13, 40), time(16, 30), 0, 135, "GBP"),
    _flight("ams-man-klm-kl307", "KLM", "KL 307", "Amsterdam", "AMS", "Manchester", "MAN",
            time(8, 30), time(8, 50), 0, 109, "EUR"),
    _flight("ber-man-easyjet-u2613", "easyJet", "U2 613", "Berlin", "BER", "Manchester", "MAN",
            time(14, 15), time(15, 10), 0, 129, "EUR"),
    _flight("lon-ams-british-airways-ba724", "British Airways", "BA 724", "London", "LCY", "Amsterdam", "AMS",
            time(8, 35), time(10, 50), 0, 115, "GBP"),
    _flight("lon-ams-klm-kl724", "KLM", "KL 724", "London", "LCY", "Amsterdam", "AMS",
            time(8, 35), time(10, 50), 0, 123, "GBP"),
    _flight("man-ams-klm-kl429", "KLM", "KL 429", "Manchester", "MAN", "Amsterdam", "AMS",
            time(15, 5), time(17, 15), 0, 99, "GBP"),
]


def find_flight(flight_id: str) -> Flight | None:
    return next((f for f in FLIGHTS if f.id.lower() == flight_id.lower()), None)
