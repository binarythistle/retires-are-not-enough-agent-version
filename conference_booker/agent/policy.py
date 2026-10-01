"""The agent's policy: what the model is told, and which tool it may be forced to call.
Everything here is a pure function of journey state, so both the plain loop and the Temporal
workflow can call it directly.
"""

from __future__ import annotations

from conference_booker.agent import tools
from conference_booker.journey import JourneyState

# Upper bound on model round-trips in one attendee turn.
MAXIMUM_STEPS = 8

NO_REPLY = "Sorry, I could not work out a reply. Could you rephrase that?"
STEPS_EXHAUSTED = "I am having trouble completing that step. Could you try rephrasing your request?"


def _date(value) -> str:
    return value.strftime("%d %b %Y")


def build_system_prompt(state: JourneyState) -> str:
    if conference := state.selected_conference:
        selected_conference_context = (
            f"The attendee has already selected {conference.name} ({conference.id}) in {conference.location}, "
            f"running {_date(conference.starts_on)} to {_date(conference.ends_on)}."
        )
    else:
        selected_conference_context = "The attendee has not selected a conference yet."

    if booking := state.conference_booking:
        conference_booking_context = (
            f"Conference booking confirmed: {booking.booking_reference}, status {booking.status}, "
            f"for {booking.conference.name}."
        )
    else:
        conference_booking_context = "The selected conference has not been booked yet."

    if details := state.traveller_details:
        traveller_details_context = (
            f"Traveller details are saved: {details.traveller_name}, departing from {details.departure_location}, "
            f"with the preference '{details.travel_preference}'."
        )
    else:
        traveller_details_context = "Traveller details have not been saved yet."

    if flight := state.selected_flight:
        selected_flight_context = (
            f"The attendee has selected {flight.airline} {flight.flight_number} ({flight.id}) from "
            f"{flight.origin_city} to {flight.destination_city}, departing at {flight.departure_time:%H:%M}."
        )
    else:
        selected_flight_context = "The attendee has not selected a flight yet."

    if flight_booking := state.flight_booking:
        flight_booking_context = (
            f"Flight booking confirmed: {flight_booking.booking_reference}, status {flight_booking.status}, "
            f"for {flight_booking.flight.airline} {flight_booking.flight.flight_number}."
        )
    else:
        flight_booking_context = "The selected flight has not been booked yet."

    if state.available_flights:
        options = "; ".join(f"{f.airline} {f.flight_number} has ID {f.id}" for f in state.available_flights)
        available_flights_context = (
            f"The most recently retrieved flight options are: {options}. "
            f"Use these IDs only when calling {tools.SELECT_FLIGHT}."
        )
    else:
        available_flights_context = "No flight options are currently retained for this journey."

    return f"""You are Conference Booker, a friendly conference travel assistant.
Welcome attendees and help them book a conference, then plan their travel.
Decide whether to use an available tool based on its
description. For conference information, use the catalogue tool
rather than inventing conference details. For any time-sensitive
decision, including listing bookable conferences, use the
current-time tool and only present conferences that have not yet
started.

Keep replies concise. When presenting more than one conference,
use one Markdown bullet per conference. Format each item as: a
bold conference name on the bullet line; dates and location on
the next line; then its description on a final line.

Start by gathering the traveller's name, departure city or
airport, and travel preference. Do not infer missing details.
When all three are clear, save them with the traveller-details
tool and confirm what was recorded. Do not list or select
conferences until traveller details have been saved.

Once traveller details are saved, only select a conference when
the attendee has clearly chosen one. First obtain its exact ID
from the catalogue if needed. After {tools.SELECT_CONFERENCE}
succeeds, summarise the choice and ask for explicit confirmation
to book the conference. Never state that a conference is selected
unless {tools.SELECT_CONFERENCE} succeeded during this turn
or a selected-conference context is supplied below.

Only use the conference-booking tool after the attendee
explicitly asks to book their selected conference. Confirm the
booking reference and status returned by the tool. Never state
that a conference is booked, confirmed, or has a booking
reference unless {tools.BOOK_SELECTED_CONFERENCE} succeeded
during this turn or a conference-booking context is supplied
below.

Only after the conference booking is confirmed, use the
flight-search tool to retrieve daily flight schedules when the
attendee asks for flights or is ready to explore travel. Present
each flight as a bold airline and flight number, followed by
route, times, and price. When the attendee clearly chooses one,
use the flight-selection tool with the exact ID returned by the
flight-search tool, then summarise it and ask for explicit
confirmation to book. Never state that a flight is selected
unless {tools.SELECT_FLIGHT} succeeded during this turn or a
selected-flight context is supplied below.

Only use the flight-booking tool after the attendee explicitly
asks to book their selected flight. Confirm the booking reference
and status returned by the tool. Never state that a flight is
booked, confirmed, or has a booking reference unless
{tools.BOOK_SELECTED_FLIGHT} succeeded during this turn or a
booking context is supplied below.

{selected_conference_context}
{conference_booking_context}
{traveller_details_context}
{selected_flight_context}
{flight_booking_context}
{available_flights_context}
Never invent flight schedules or select or book a flight on the attendee's behalf."""


def get_required_tool(state: JourneyState, user_message: str) -> str | None:
    """The tool the model must call this step, or None to let it decide."""
    if state.selected_flight and not state.flight_booking and _is_explicit_booking_instruction(user_message):
        return tools.BOOK_SELECTED_FLIGHT

    if (
        state.selected_conference
        and not state.conference_booking
        and not state.selected_flight
        and _is_explicit_booking_instruction(user_message)
    ):
        return tools.BOOK_SELECTED_CONFERENCE

    if not state.selected_flight and any(
        _normalize(flight.flight_number) in _normalize(user_message) for flight in state.available_flights
    ):
        return tools.SELECT_FLIGHT

    return None


def create_completion_summary(state: JourneyState) -> str:
    conference_booking = state.conference_booking
    flight_booking = state.flight_booking
    if conference_booking is None or flight_booking is None:
        raise ValueError("A completed journey must have a conference booking and a flight booking.")

    flight = flight_booking.flight
    return f"""Journey complete

**Conference:** {conference_booking.conference.name}
Conference booking: {conference_booking.booking_reference} · {conference_booking.status}

**Flight:** {flight.airline} {flight.flight_number} · {flight.origin_city} to {flight.destination_city}
Flight booking: {flight_booking.booking_reference} · {flight_booking.status}

Your conference and flight are confirmed. This demo workflow is complete."""


_BOOKING_PHRASES = ("BOOKIT", "BOOKTHIS", "BOOKTHE", "BOOKMY", "CONFIRMIT", "CONFIRMBOOKING", "GOAHEAD")


def _is_explicit_booking_instruction(user_message: str) -> bool:
    normalized = _normalize(user_message)
    return normalized in ("BOOK", "CONFIRM") or any(phrase in normalized for phrase in _BOOKING_PHRASES)


def _normalize(value: str) -> str:
    return "".join(c.upper() for c in value if c.isalnum())
