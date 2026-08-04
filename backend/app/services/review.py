"""The review queue: matching a proposal to a trip, and the accept boundary.

This module holds the line the whole design is built around:

    **Accepting is the only thing here that may touch trip data.**

`find_matching_trip` reads. `reject_extraction` only flips a status. Only
`accept_extraction` creates or modifies a `Trip`, `Stay`, `Leg`, or
`CountryEntry`, and it goes through the same `services.trips` derived-state
functions a manual edit does, so an accepted booking and a typed one cannot
drift apart.

Two rules from the domain shape the matching:

- **A trip is one country.** A booking for a different country than a trip is in
  must *propose a new trip*, never attach to it -- attaching would later fail
  the one-country guard. So a mismatched country is filtered out at match time
  and the proposal falls through to "new trip".
- **The stay ends when you leave, not at the last checkout.** Matching works off
  the trip's denormalised span, which already accounts for the leaving date.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.models import (
    EmailMessage,
    Leg,
    LearnedRule,
    Stay,
    Trip,
    TravelMode,
    Extraction,
    ExtractionStatus,
    utcnow,
)
from app.services.email_filter import effective_rules, is_sender_covered, sender_domain
from app.services.extraction import Booking, validate_booking
from app.services.geocode import fill_coordinates
from app.services.trips import (
    refresh_trip_dates,
    sync_country_entries,
    trip_country_code,
)

log = logging.getLogger("yayo.review")

# How far a booking may sit outside a trip's span and still be judged part of
# it. A flight home the day after the last checkout, a hotel booked to start the
# evening you land -- two days of slack absorbs the ordinary edges without
# swallowing a separate trip.
MATCH_SLACK_DAYS = 2

_HOTEL = "hotel"
_MODE_BY_KIND = {
    "flight": TravelMode.flight,
    "train": TravelMode.train,
    "bus": TravelMode.bus,
    "ferry": TravelMode.ferry,
    "car": TravelMode.car,
}


# --------------------------------------------------------------------------
# Matching -- read-only. Proposes where a booking would go; changes nothing.
# --------------------------------------------------------------------------


def _booking_span(booking: Booking) -> Optional[tuple[date, date]]:
    if booking.start_date is None:
        return None
    start = date.fromisoformat(booking.start_date)
    end = date.fromisoformat(booking.end_date) if booking.end_date else start
    return start, end


def _overlaps(
    a: tuple[date, date], b: tuple[date, date], slack: int
) -> bool:
    """Whether [a] falls within [b] widened by `slack` days on each side."""
    (a_start, a_end), (b_start, b_end) = a, b
    return (
        (a_start - b_start).days <= slack + (b_end - b_start).days
        and (b_start - a_start).days <= slack + (a_end - a_start).days
    )


def find_matching_trip(session: Session, booking: Booking) -> Optional[int]:
    """The id of the trip this booking belongs to, or None to propose a new one.

    A match needs date overlap within the slack. When the booking names a
    country, the trip must be in that same country -- a different country is not
    a match, it is a new trip. When the booking names no country (a bare leg),
    date overlap alone decides, but only if it is unambiguous.
    """
    span = _booking_span(booking)
    if span is None:
        return None

    matches: list[tuple[int, date]] = []
    trips = session.exec(
        select(Trip)
        .where(Trip.start_date.is_not(None))
        .where(Trip.end_date.is_not(None))
    ).all()

    for trip in trips:
        if not _overlaps(span, (trip.start_date, trip.end_date), MATCH_SLACK_DAYS):
            continue
        if booking.country_code is not None:
            code = trip_country_code(session, trip.id)
            # A trip whose country is known and different is a different trip.
            if code is not None and code != booking.country_code:
                continue
        matches.append((trip.id, trip.start_date))

    if not matches:
        return None
    if len(matches) > 1 and booking.country_code is None:
        # A dateless-country booking overlapping several trips is ambiguous;
        # let the human decide rather than guess.
        return None
    # Closest by start date.
    b_start = span[0]
    return min(matches, key=lambda m: abs((m[1] - b_start).days))[0]


def suggest(session: Session, extraction: Extraction) -> Optional[int]:
    """Record a match suggestion on the extraction. Touches no trip data.

    Writing `suggested_trip_id` is extraction bookkeeping -- a pointer for the
    reviewer -- not a change to any trip.
    """
    booking = validate_booking(json.loads(extraction.payload_json))
    if booking is None:
        return None
    extraction.suggested_trip_id = find_matching_trip(session, booking)
    session.add(extraction)
    return extraction.suggested_trip_id


# --------------------------------------------------------------------------
# The accept boundary -- the only writer of trip data in this module
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptResult:
    trip_id: int
    created_new_trip: bool
    stay_id: Optional[int] = None
    leg_id: Optional[int] = None


class NotAcceptable(ValueError):
    """The stored booking cannot be turned into a valid trip record as-is."""


def _at(day: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(day) if day else None


def _guard_single_country(session: Session, trip_id: int, code: Optional[str]) -> None:
    """Defence in depth. The matcher never proposes a mismatched trip, but a
    trip's country could change between suggestion and accept."""
    if not code:
        return
    existing = trip_country_code(session, trip_id)
    if existing is not None and existing != code:
        raise NotAcceptable(
            f"trip {trip_id} is a stay in {existing}, not {code}; "
            "accept would split it across two countries"
        )


def _check_acceptable(booking: Booking) -> None:
    """Refuse a booking that cannot become a valid record -- *before* any trip
    is created, so a failed accept writes nothing at all."""
    if booking.kind == _HOTEL:
        if not booking.city:
            raise NotAcceptable("a hotel needs a city; edit the proposal first")
        if booking.start_date is None:
            raise NotAcceptable(
                "a hotel needs a check-in date; edit the proposal first"
            )


def _apply_hotel(session: Session, trip_id: int, booking: Booking) -> Stay:
    check_in = date.fromisoformat(booking.start_date)
    # Same-day is a valid day-room; a missing checkout means one night unset,
    # so fall back to the check-in and let the reviewer extend it.
    check_out = (
        date.fromisoformat(booking.end_date) if booking.end_date else check_in
    )
    stay = Stay(
        trip_id=trip_id,
        country_code=booking.country_code or "",
        city=booking.city,
        hotel_name=booking.hotel_name or "",
        confirmation_code=booking.confirmation_code or "",
        booking_source="email",
        check_in=check_in,
        check_out=check_out,
    )
    fill_coordinates(stay)
    session.add(stay)
    session.commit()
    session.refresh(stay)
    return stay


def _apply_leg(session: Session, trip_id: int, booking: Booking) -> Leg:
    leg = Leg(
        trip_id=trip_id,
        mode=_MODE_BY_KIND.get(booking.kind, TravelMode.flight),
        country_code=booking.country_code or "",
        carrier=booking.carrier or "",
        confirmation_code=booking.confirmation_code or "",
        depart_at=_at(booking.start_date),
    )
    session.add(leg)
    session.commit()
    session.refresh(leg)
    return leg


ALLOWED_OVERRIDES = frozenset(
    {
        "kind",
        "country_code",
        "city",
        "start_date",
        "end_date",
        "hotel_name",
        "carrier",
        "confirmation_code",
    }
)


def _learn_sender_if_new(session: Session, extraction: Extraction) -> None:
    """D3: accepting is what proves a manually-extracted sender is real.

    A no-op for the ordinary auto-extracted case -- that sender already
    passed the filter, so `is_sender_covered` is already true and nothing is
    written. It only actually inserts a row for the sender of a message that
    reached extraction through the manual bypass (D2), and only once its
    proposal has been accepted, per D3's "learn on accept, not on extract".
    """
    email = session.get(EmailMessage, extraction.email_message_id)
    if email is None or not email.from_addr:
        return
    domain = sender_domain(email.from_addr)
    if not domain:
        return
    if is_sender_covered(email.from_addr, effective_rules(session)):
        return
    if session.exec(select(LearnedRule).where(LearnedRule.domain == domain)).first():
        return
    session.add(LearnedRule(domain=domain, source="manual_extract_accept"))
    session.commit()
    log.info(
        "learned sender domain %s from accepted extraction %s", domain, extraction.id
    )


def accept_extraction(
    session: Session,
    extraction: Extraction,
    overrides: Optional[dict] = None,
) -> AcceptResult:
    """Turn a pending proposal into real trip data. The only writer here.

    `overrides` lets the reviewer correct the model's reading before it lands --
    a missing city, a wrong date. Only the booking fields may be overridden;
    anything else is ignored so the review form cannot smuggle in a trip id.

    Attaches to the suggested trip, or creates a new one when there is no
    suggestion -- which is exactly what a different-country booking gets, so the
    one-country rule is upheld by construction rather than by catching an error.
    """
    if extraction.status != ExtractionStatus.pending:
        raise NotAcceptable(f"extraction {extraction.id} is already {extraction.status}")

    payload = json.loads(extraction.payload_json)
    if overrides:
        payload.update(
            {k: v for k, v in overrides.items() if k in ALLOWED_OVERRIDES}
        )
    booking = validate_booking(payload)
    if booking is None:
        raise NotAcceptable("the stored payload is not a usable booking")

    # Everything that could refuse the booking runs before a trip is created,
    # so a failed accept leaves no orphan trip behind.
    _check_acceptable(booking)

    trip_id = extraction.suggested_trip_id
    created_new_trip = False
    if trip_id is None:
        trip = Trip(notes="")
        session.add(trip)
        session.commit()
        session.refresh(trip)
        trip_id = trip.id
        created_new_trip = True
    else:
        trip = session.get(Trip, trip_id)
        if trip is None:
            raise NotAcceptable(f"suggested trip {trip_id} no longer exists")

    _guard_single_country(session, trip_id, booking.country_code)

    if booking.kind == _HOTEL:
        stay = _apply_hotel(session, trip_id, booking)
        applied = AcceptResult(trip_id, created_new_trip, stay_id=stay.id)
    else:
        leg = _apply_leg(session, trip_id, booking)
        applied = AcceptResult(trip_id, created_new_trip, leg_id=leg.id)

    # Same derived-state path a manual edit takes.
    refresh_trip_dates(session, trip)
    session.commit()
    sync_country_entries(session, trip)

    extraction.status = ExtractionStatus.accepted
    extraction.suggested_trip_id = trip_id
    extraction.reviewed_at = utcnow()
    extraction.applied_ids_json = json.dumps(applied.__dict__, sort_keys=True)
    session.add(extraction)
    session.commit()

    log.info(
        "accepted extraction %s -> trip %d (%s)",
        extraction.id,
        trip_id,
        "new" if created_new_trip else "existing",
    )
    _learn_sender_if_new(session, extraction)
    return applied


def reject_extraction(session: Session, extraction: Extraction) -> None:
    """Dismiss a proposal. Touches no trip data -- only the extraction's status."""
    if extraction.status != ExtractionStatus.pending:
        raise NotAcceptable(f"extraction {extraction.id} is already {extraction.status}")
    extraction.status = ExtractionStatus.rejected
    extraction.reviewed_at = utcnow()
    session.add(extraction)
    session.commit()
