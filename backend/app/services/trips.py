"""Derived trip state.

Anything computed from a trip's segments lives here rather than in the route
handlers, so the email pipeline (stage 8) and manual edits go through identical
logic and cannot drift apart.
"""

from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.countries import country_name
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


def trip_label(trip: Trip, stays: list[Stay]) -> str:
    """What to call a trip when nobody typed a name for it.

    A journey that crosses borders is identified by the countries; one that
    stays inside a single country is identified by the cities in it, because
    naming it after the country would say nothing you did not already know.
    An explicit title always wins, so imported or hand-named trips keep theirs.
    """
    if trip.title.strip():
        return trip.title.strip()
    if not stays:
        return "New trip"

    ordered = sorted(stays, key=lambda s: s.check_in)

    countries: list[str] = []
    for stay in ordered:
        code = stay.country_code.upper()
        if code and code not in countries:
            countries.append(code)

    if len(countries) > 1:
        names = [country_name(c) for c in countries]
        if len(names) <= 3:
            return " → ".join(names)
        return f"{' → '.join(names[:2])} +{len(names) - 2} more"

    cities: list[str] = []
    for stay in ordered:
        if stay.city and stay.city not in cities:
            cities.append(stay.city)

    if not cities:
        return country_name(countries[0]) if countries else "New trip"
    if len(cities) == 1:
        hotel = ordered[0].hotel_name.strip()
        return f"{cities[0]} · {hotel}" if hotel else cities[0]
    if len(cities) <= 3:
        return " → ".join(cities)
    return f"{' → '.join(cities[:2])} +{len(cities) - 2} more"


def _period_start(stays: list[Stay], legs: list[Leg]) -> Optional[date]:
    """When you are first in the country: the arrival, or the first check-in."""
    candidates: list[date] = [s.check_in for s in stays]
    for leg in legs:
        when = leg.arrive_at or leg.depart_at
        if when:
            candidates.append(when.date())
    return min(candidates) if candidates else None


def _uncovered(
    start: Optional[date], end: Optional[date], stays: list[Stay]
) -> list[dict]:
    """Nights inside a country with no hotel booked.

    Being in a country from the 30th to the 6th with hotels covering only the
    30th to the 3rd means three nights with nowhere to sleep -- almost always a
    booking that was forgotten rather than a deliberate choice. Walks the stays
    in order and reports every stretch nobody covers, including before the first
    hotel and after the last.
    """
    if start is None or end is None or end <= start:
        return []

    gaps: list[tuple[date, date]] = []
    cursor = start
    for stay in sorted(stays, key=lambda s: s.check_in):
        if stay.check_in > cursor:
            gaps.append((cursor, min(stay.check_in, end)))
        if stay.check_out > cursor:
            cursor = stay.check_out
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))

    return [
        {"from": str(a), "to": str(b), "nights": (b - a).days}
        for a, b in gaps
        if b > a
    ]


def country_segments(session: Session, trip: Trip) -> list[dict]:
    """The trip broken into the countries it visits, in arrival order.

    This is the shape the app is actually about: which country, on which
    passport, and which hotels while there. Stays and arrival legs are folded
    into the country they belong to so none of that has to be reassembled by
    eye.
    """
    stays = session.exec(
        select(Stay).where(Stay.trip_id == trip.id).order_by(Stay.check_in)
    ).all()
    legs = session.exec(
        select(Leg).where(Leg.trip_id == trip.id).order_by(Leg.depart_at)
    ).all()
    entries = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip.id)
    ).all()
    entry_by_code = {e.country_code.upper(): e for e in entries}

    order: list[str] = []
    grouped: dict[str, list[Stay]] = {}
    for stay in stays:
        code = stay.country_code.upper()
        if code not in grouped:
            grouped[code] = []
            order.append(code)
        grouped[code].append(stay)

    # A leg can be recorded before its country has any hotel booked, so its
    # country still needs a section.
    for leg in legs:
        code = leg.country_code.upper()
        if code and code not in grouped:
            grouped[code] = []
            order.append(code)

    raw = []
    for code in order:
        group = grouped[code]
        arrivals = [leg for leg in legs if leg.country_code.upper() == code]
        raw.append((code, group, arrivals, _period_start(group, arrivals)))

    # Countries in the order you are actually in them. Anything with no date at
    # all sorts last, since it cannot bound anything.
    raw.sort(key=lambda r: (r[3] is None, r[3] or date.max))

    segments = []
    for index, (code, group, arrivals, starts_on) in enumerate(raw):
        entry = entry_by_code.get(code)

        # You leave a country when the next one begins. For the final country
        # there is nothing to bound it -- return travel is not tracked -- so it
        # ends at the last checkout and can never report a trailing gap.
        next_start = next(
            (r[3] for r in raw[index + 1 :] if r[3] is not None), None
        )
        ends_on = next_start or (
            max((s.check_out for s in group), default=None) if group else None
        )

        segments.append(
            {
                "country_code": code,
                "country_name": country_name(code),
                "entry": entry.model_dump() if entry else None,
                "passport_id": entry.passport_id if entry else None,
                "entered_on": str(entry.entered_on) if entry else None,
                "starts_on": str(starts_on) if starts_on else None,
                "ends_on": str(ends_on) if ends_on else None,
                "nights": sum(s.nights for s in group),
                "unbooked": _uncovered(starts_on, ends_on, group),
                "stays": [{**s.model_dump(), "nights": s.nights} for s in group],
                "legs": [leg.model_dump() for leg in arrivals],
            }
        )
    return segments


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
        # `notes` stays the trip's own free-text memo, matching list_trips. The
        # Note records go under notes_list -- spreading model_dump() and then
        # writing "notes" here would silently replace the memo with an array.
        **trip.model_dump(),
        "label": trip_label(trip, stays),
        "status": trip_status(trip),
        "countries": sorted({s.country_code for s in stays}),
        "nights": sum(s.nights for s in stays),
        "stays": [{**s.model_dump(), "nights": s.nights} for s in stays],
        "legs": [leg.model_dump() for leg in legs],
        "requirements": [r.model_dump() for r in trip.requirements],
        "countries_visited": [
            {"code": c, "name": country_name(c)}
            for c in dict.fromkeys(s.country_code.upper() for s in stays)
        ],
        "country_segments": country_segments(session, trip),
        "entries": [e.model_dump() for e in entries],
        "notes_list": [n.model_dump() for n in notes],
    }
