"""The tools that only validate input and move journey state. They are pure functions of state:
no I/O, so the Temporal workflow will be able to run them inline, exactly as the plain loop does.
"""

from __future__ import annotations

import re

from conference_booker.agent import tools
from conference_booker.agent.results import (
    ConferenceSelectionResult,
    FlightSelectionResult,
    TravellerDetailsResult,
)
from conference_booker.journey import JourneyState, TravellerDetails

# Python's re has no \p{L}; [^\W\d_] is "any Unicode letter".
_LETTER = r"[^\W\d_]"
TRAVELLER_NAME_PATTERN = re.compile(rf"^{_LETTER}(?:{_LETTER}|[ .'\-]){{1,99}}$")
DEPARTURE_LOCATION_PATTERN = re.compile(rf"^{_LETTER}(?:{_LETTER}|[ .,'()/\-]){{1,99}}$")


def save_traveller_details(
    state: JourneyState,
    traveller_name: str,
    departure_location: str,
    travel_preference: str,
) -> tuple[JourneyState, TravellerDetailsResult]:
    errors: list[str] = []

    if not TRAVELLER_NAME_PATTERN.match(traveller_name.strip()):
        errors.append("Provide the traveller's name using at least two letters.")

    if not DEPARTURE_LOCATION_PATTERN.match(departure_location.strip()):
        errors.append("Provide a valid departure city or airport.")

    if not travel_preference.strip() or len(travel_preference.strip()) > 160:
        errors.append("Provide a travel preference of up to 160 characters.")

    if errors:
        return state, TravellerDetailsResult(saved=False, details=None, errors=errors)

    details = TravellerDetails(
        traveller_name=traveller_name.strip(),
        departure_location=departure_location.strip(),
        travel_preference=travel_preference.strip(),
    )

    # Saving traveller details invalidates anything downstream of them.
    next_state = state.model_copy(
        update={
            "traveller_details": details,
            "conference_booking": None,
            "available_flights": [],
            "selected_flight": None,
            "flight_booking": None,
            "is_complete": False,
        }
    )
    return next_state, TravellerDetailsResult(saved=True, details=details, errors=[])


def select_conference(state: JourneyState, conference_id: str) -> tuple[JourneyState, ConferenceSelectionResult]:
    # Validates against the catalogue already retained in state, so selecting costs no HTTP call.
    if not state.available_conferences:
        return state, ConferenceSelectionResult(
            selected=False,
            conference=None,
            error=f"Call {tools.GET_AVAILABLE_CONFERENCES} before selecting a conference.",
        )

    selected = next((c for c in state.available_conferences if c.id.lower() == conference_id.lower()), None)
    if selected is None:
        return state, ConferenceSelectionResult(
            selected=False,
            conference=None,
            error=f"No conference with the ID '{conference_id}' is available to select.",
        )

    next_state = state.model_copy(
        update={
            "selected_conference": selected,
            "conference_booking": None,
            "available_flights": [],
            "selected_flight": None,
            "flight_booking": None,
            "is_complete": False,
        }
    )
    return next_state, ConferenceSelectionResult(selected=True, conference=selected, error=None)


def select_flight(state: JourneyState, flight_id: str) -> tuple[JourneyState, FlightSelectionResult]:
    if state.selected_conference is None or state.traveller_details is None:
        return state, FlightSelectionResult(
            selected=False,
            flight=None,
            error="Select a conference and save traveller details before selecting a flight.",
        )

    if not state.available_flights:
        return state, FlightSelectionResult(
            selected=False,
            flight=None,
            error=f"Call {tools.GET_FLIGHT_OPTIONS} before selecting a flight.",
        )

    flight = next((f for f in state.available_flights if f.id.lower() == flight_id.lower()), None)
    if flight is None:
        return state, FlightSelectionResult(
            selected=False, flight=None, error="That flight is not available for the saved journey."
        )

    next_state = state.model_copy(update={"selected_flight": flight, "flight_booking": None, "is_complete": False})
    return next_state, FlightSelectionResult(selected=True, flight=flight, error=None)
