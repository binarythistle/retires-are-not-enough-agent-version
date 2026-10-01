"""The tools the model is told about, in OpenAI Chat Completions form. These are declarations
only: the model can ask for a call, but the agent loop decides what actually runs.
"""

from __future__ import annotations

from typing import Any

GET_CURRENT_DATE_TIME = "get_current_date_time"
GET_AVAILABLE_CONFERENCES = "get_available_conferences"
SELECT_CONFERENCE = "select_conference"
BOOK_SELECTED_CONFERENCE = "book_selected_conference"
SAVE_TRAVELLER_DETAILS = "save_traveller_details"
GET_FLIGHT_OPTIONS = "get_flight_options"
SELECT_FLIGHT = "select_flight"
BOOK_SELECTED_FLIGHT = "book_selected_flight"


def _schema(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    properties = properties or {}
    return {
        "type": "object",
        "required": list(properties),
        "properties": properties,
        "additionalProperties": False,
    }


def _declare(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"description": description, "name": name, "parameters": parameters}}


DECLARATIONS: list[dict[str, Any]] = [
    _declare(
        GET_CURRENT_DATE_TIME,
        "Gets the current local date and time. Use it for every time-sensitive operation, including deciding whether a conference is in the future and bookable, or when planning flights and hotels. Do not assume the current date.",
        _schema(),
    ),
    _declare(
        GET_AVAILABLE_CONFERENCES,
        "Gets conferences and their dates from the Conference Catalog API. Use it when the attendee asks what conferences or events are available, asks about a specific conference, or needs help choosing one. Use the current-time tool as well when deciding which conferences are in the future and bookable.",
        _schema(),
    ),
    _declare(
        SELECT_CONFERENCE,
        "Records the attendee's selected conference. Call this only after they clearly choose a conference. The conferenceId must exactly match an ID returned by get_available_conferences. If the ID is unknown, call get_available_conferences first.",
        _schema({"conferenceId": {"type": "string", "description": "Exact conference ID from get_available_conferences."}}),
    ),
    _declare(
        BOOK_SELECTED_CONFERENCE,
        "Creates a confirmed conference ticket booking for the selected conference and saved traveller. Call it only after the attendee explicitly asks to book, such as 'book it' or 'confirm booking'. It takes no arguments because it uses the selected conference and saved traveller details. Never call it merely because a conference was selected.",
        _schema(),
    ),
    _declare(
        SAVE_TRAVELLER_DETAILS,
        "Validates and saves traveller details for the selected conference. Call it only after the attendee has clearly supplied all three values: travellerName, departureLocation as a city or airport, and travelPreference. Never infer missing values.",
        _schema(
            {
                "travellerName": {"type": "string"},
                "departureLocation": {"type": "string", "description": "Departure city or airport."},
                "travelPreference": {"type": "string"},
            }
        ),
    ),
    _declare(
        GET_FLIGHT_OPTIONS,
        "Gets daily flight schedules from the saved departure city or airport to the selected conference city. Call it only after a conference booking and traveller details have been saved, when the attendee asks for flights or wants to plan travel. It takes no arguments because it uses the saved journey state.",
        _schema(),
    ),
    _declare(
        SELECT_FLIGHT,
        "Records the attendee's chosen flight for the saved journey. Call it only after they clearly select a flight. The flightId must exactly match an ID returned by get_flight_options for this journey. If the attendee has not seen flight options or their choice is unclear, call get_flight_options or ask a follow-up question first.",
        _schema({"flightId": {"type": "string", "description": "Exact flight ID from get_flight_options."}}),
    ),
    _declare(
        BOOK_SELECTED_FLIGHT,
        "Creates a confirmed booking for the selected flight and saved traveller. Call it only after the attendee explicitly asks to book, such as 'book it' or 'confirm booking'. It takes no arguments because it uses the selected flight and saved traveller details. Never call it merely because a flight was selected.",
        _schema(),
    ),
]
