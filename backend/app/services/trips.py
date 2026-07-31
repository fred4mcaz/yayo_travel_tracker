"""Derived trip state.

Anything computed from a trip's segments lives here rather than in the route
handlers, so the email pipeline (stage 8) and manual edits go through identical
logic and cannot drift apart.
"""

from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models import CountryEntry, Leg, Note, Stay, Trip, utcnow


def refresh_trip_dates(session: Session, trip: Trip) -> Trip:
    """Recompute the denormalised start/end span from the trip's segments.

    Must be called after any stay or leg change. Legs count too: a red-eye that
    departs the day before the first check-in still belongs to the trip.
    """
    stays = session.exec(select(Stay).where(Stay.trip_id == trip.id)).all()
    legs = session.exec(select(Leg).where(Leg.trip_id == trip.id)).all()

    starts: list[date] = [s.check_in for s in stays]
    ends: list[date] = [s.check_out for s in stays]
    for leg in legs:
        if leg.depart_at:
            starts.append(leg.depart_at.date())
            ends.append(leg.depart_at.date())
        if leg.arrive_at:
            starts.append(leg.arrive_at.date())
            ends.append(leg.arrive_at.date())

    trip.start_date = min(starts) if starts else None
    trip.end_date = max(ends) if ends else None
    trip.updated_at = utcnow()
    session.add(trip)
    return trip


def trip_status(trip: Trip, today: Optional[date] = None) -> str:
    """past | ongoing | future | undated."""
    today = today or date.today()
    if trip.start_date is None or trip.end_date is None:
        return "undated"
    if trip.end_date < today:
        return "past"
    if trip.start_date > today:
        return "future"
    return "ongoing"


def sync_country_entries(session: Session, trip: Trip) -> list[CountryEntry]:
    """Ensure one CountryEntry per country the trip's stays visit.

    Creates missing rows so the passport picker has something to attach to, and
    removes rows for countries no longer visited. Existing rows are never
    overwritten — once you record that you entered Japan on the US passport,
    editing an unrelated hotel must not silently discard that.
    """
    stays = session.exec(
        select(Stay).where(Stay.trip_id == trip.id).order_by(Stay.check_in)
    ).all()

    # First arrival date per country, in visit order.
    first_seen: dict[str, date] = {}
    for stay in stays:
        code = stay.country_code.upper()
        if code not in first_seen or stay.check_in < first_seen[code]:
            first_seen[code] = stay.check_in

    existing = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip.id)
    ).all()
    by_code = {e.country_code.upper(): e for e in existing}

    for code, entered_on in first_seen.items():
        if code in by_code:
            continue
        session.add(
            CountryEntry(
                trip_id=trip.id,
                country_code=code,
                entered_on=entered_on,
                passport_id=_last_passport_used_for(session, code),
            )
        )

    for code, entry in by_code.items():
        if code not in first_seen:
            session.delete(entry)

    session.commit()
    return session.exec(
        select(CountryEntry)
        .where(CountryEntry.trip_id == trip.id)
        .order_by(CountryEntry.entered_on)
    ).all()


def _last_passport_used_for(session: Session, country_code: str) -> Optional[int]:
    """Default to whichever passport was last used for this country.

    Re-entering on a different passport than last time is unusual and usually a
    mistake, so the prior choice is the right default.
    """
    prior = session.exec(
        select(CountryEntry)
        .where(CountryEntry.country_code == country_code)
        .where(CountryEntry.passport_id.is_not(None))
        .order_by(CountryEntry.entered_on.desc())
    ).first()
    return prior.passport_id if prior else None


def trip_detail(session: Session, trip: Trip) -> dict:
    """Full trip payload: the shape the frontend's detail panel consumes."""
    stays = session.exec(
        select(Stay).where(Stay.trip_id == trip.id).order_by(Stay.check_in)
    ).all()
    legs = session.exec(
        select(Leg).where(Leg.trip_id == trip.id).order_by(Leg.depart_at)
    ).all()
    entries = session.exec(
        select(CountryEntry)
        .where(CountryEntry.trip_id == trip.id)
        .order_by(CountryEntry.entered_on)
    ).all()
    notes = session.exec(
        select(Note).where(Note.trip_id == trip.id).order_by(Note.on_date)
    ).all()

    return {
        **trip.model_dump(),
        "status": trip_status(trip),
        "countries": sorted({s.country_code for s in stays}),
        "nights": sum(s.nights for s in stays),
        "stays": [{**s.model_dump(), "nights": s.nights} for s in stays],
        "legs": [leg.model_dump() for leg in legs],
        "requirements": [r.model_dump() for r in trip.requirements],
        "entries": [e.model_dump() for e in entries],
        "notes": [n.model_dump() for n in notes],
    }
