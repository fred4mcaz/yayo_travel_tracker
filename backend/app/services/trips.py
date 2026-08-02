"""Derived trip state.

Anything computed from a trip's contents lives here rather than in the route
handlers, so the email pipeline (stage 8) and manual edits go through identical
logic and cannot drift apart.

A trip is one international stay in one country. That is enforced in the API,
so everything here can assume a single country and say so plainly.
"""

from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.countries import country_name
from app.models import (
    CountryEntry,
    Leg,
    MergeDismissal,
    Note,
    Requirement,
    Stay,
    Trip,
    utcnow,
)


def refresh_trip_dates(session: Session, trip: Trip) -> Trip:
    """Recompute the denormalised start/end span from the trip's contents.

    Must be called after any change. Legs count because a red-eye departing the
    night before the first check-in still belongs to the trip, and the leaving
    date counts because the stay is not over when the last hotel ends -- that
    is precisely the gap worth seeing.
    """
    stays = session.exec(select(Stay).where(Stay.trip_id == trip.id)).all()
    legs = session.exec(select(Leg).where(Leg.trip_id == trip.id)).all()
    entry = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip.id)
    ).first()

    starts: list[date] = [s.check_in for s in stays]
    ends: list[date] = [s.check_out for s in stays]
    for leg in legs:
        if leg.depart_at:
            starts.append(leg.depart_at.date())
            ends.append(leg.depart_at.date())
        if leg.arrive_at:
            starts.append(leg.arrive_at.date())
            ends.append(leg.arrive_at.date())
    if entry and entry.exited_on:
        ends.append(entry.exited_on)

    trip.start_date = min(starts) if starts else None
    trip.end_date = max(ends) if ends else None
    trip.updated_at = utcnow()
    session.add(trip)
    return trip


def trip_country_code(session: Session, trip_id: int) -> Optional[str]:
    """The country this trip is a stay in, or None if nothing is recorded yet."""
    stay = session.exec(select(Stay).where(Stay.trip_id == trip_id)).first()
    if stay is not None:
        return stay.country_code.upper()
    leg = session.exec(
        select(Leg).where(Leg.trip_id == trip_id).where(Leg.country_code != "")
    ).first()
    return leg.country_code.upper() if leg else None


def trip_label(stays: list[Stay]) -> str:
    """What to call a trip. Always derived -- trips are never named by hand.

    The country is already shown beside it, so the label is the cities. One
    stop reads as "Hanoi · Sofitel Legend", several read as the route.
    """
    if not stays:
        return "New trip"

    ordered = sorted(stays, key=lambda s: s.check_in)
    cities: list[str] = []
    for stay in ordered:
        if stay.city and stay.city not in cities:
            cities.append(stay.city)

    if not cities:
        return "New trip"
    if len(cities) == 1:
        hotel = ordered[0].hotel_name.strip()
        return f"{cities[0]} · {hotel}" if hotel else cities[0]
    if len(cities) <= 3:
        return " → ".join(cities)
    return f"{' → '.join(cities[:2])} +{len(cities) - 2} more"


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


def unbooked_nights(
    start: Optional[date], end: Optional[date], stays: list[Stay]
) -> list[dict]:
    """Nights inside the country with no hotel booked.

    Being in Vietnam from the 30th to the 6th with bookings covering only the
    30th to the 3rd means three nights with nowhere to sleep -- almost always a
    booking that was forgotten. Walks the stays in order and reports every
    stretch nobody covers, including before the first hotel and after the last.
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


def trip_country(session: Session, trip: Trip) -> Optional[dict]:
    """The country this trip is a stay in, with everything that hangs off it.

    Returns None for a trip with nothing recorded yet.

    The stay ends on the date you say you are leaving, if you have said. That
    matters: without it the stay can only end at the last checkout, so "two
    weeks in Vietnam, first four nights booked" -- the most common way to have
    forgotten a hotel -- would look complete. Leaving it blank is fine; the
    stay then ends at the last checkout and only gaps between bookings show.
    """
    stays = session.exec(
        select(Stay).where(Stay.trip_id == trip.id).order_by(Stay.check_in)
    ).all()
    legs = session.exec(
        select(Leg).where(Leg.trip_id == trip.id).order_by(Leg.depart_at)
    ).all()
    if not stays and not legs:
        return None

    code = trip_country_code(session, trip.id) or ""
    entry = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip.id)
    ).first()

    # You are in the country from whichever comes first: landing, or checking in.
    starts: list[date] = [s.check_in for s in stays]
    for leg in legs:
        when = leg.arrive_at or leg.depart_at
        if when:
            starts.append(when.date())
    starts_on = min(starts) if starts else None
    last_checkout = max((s.check_out for s in stays), default=None)
    leaving_on = entry.exited_on if entry else None
    ends_on = leaving_on or last_checkout

    return {
        "country_code": code,
        "country_name": country_name(code),
        "entry": entry.model_dump() if entry else None,
        "passport_id": entry.passport_id if entry else None,
        "entered_on": str(entry.entered_on) if entry else None,
        "leaving_on": str(leaving_on) if leaving_on else None,
        "starts_on": str(starts_on) if starts_on else None,
        "ends_on": str(ends_on) if ends_on else None,
        "nights": sum(s.nights for s in stays),
        "unbooked": unbooked_nights(starts_on, ends_on, stays),
        "stays": [{**s.model_dump(), "nights": s.nights} for s in stays],
        "legs": [leg.model_dump() for leg in legs],
    }


def sync_country_entries(session: Session, trip: Trip) -> Optional[CountryEntry]:
    """Keep exactly one CountryEntry, for the one country the trip is in.

    Never overwrites an existing row's passport: once you record that you
    entered Japan on the US passport, editing an unrelated hotel must not
    silently discard that.
    """
    code = trip_country_code(session, trip.id)
    existing = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip.id)
    ).all()

    if code is None:
        for row in existing:
            session.delete(row)
        session.commit()
        return None

    entered_on = min(
        (s.check_in for s in session.exec(
            select(Stay).where(Stay.trip_id == trip.id)
        ).all()),
        default=None,
    )
    if entered_on is None:
        arrivals = [
            leg.arrive_at or leg.depart_at
            for leg in session.exec(select(Leg).where(Leg.trip_id == trip.id)).all()
        ]
        dated = [a.date() for a in arrivals if a]
        entered_on = min(dated) if dated else date.today()

    keeper: Optional[CountryEntry] = None
    for row in existing:
        if keeper is None and row.country_code.upper() == code:
            keeper = row
        else:
            session.delete(row)

    if keeper is None:
        keeper = CountryEntry(
            trip_id=trip.id,
            country_code=code,
            entered_on=entered_on,
            passport_id=_last_passport_used_for(session, code),
        )
    else:
        keeper.entered_on = entered_on
    session.add(keeper)
    session.commit()
    session.refresh(keeper)
    return keeper


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


# How far apart two same-country trips may sit and still be offered as one to
# merge. Generous on purpose: automatic email matching stays strict (an
# out-of-span hotel makes a new trip), and this is only a *suggestion* the human
# confirms -- wide enough to catch "first four nights, then a hotel a week later
# landed as its own trip", which is exactly what merge is for.
MERGE_ADJACENCY_DAYS = 30


def _dismissed_partner_ids(session: Session, trip_id: int) -> set[int]:
    """Trip ids this trip has been deliberately kept separate from.

    Symmetric: a dismissal is stored once as an unordered pair, so this looks at
    both columns and returns whichever id is not this trip.
    """
    rows = session.exec(
        select(MergeDismissal).where(
            (MergeDismissal.trip_low_id == trip_id)
            | (MergeDismissal.trip_high_id == trip_id)
        )
    ).all()
    return {
        row.trip_high_id if row.trip_low_id == trip_id else row.trip_low_id
        for row in rows
    }


def keep_trips_separate(session: Session, trip_id_a: int, trip_id_b: int) -> None:
    """Record that these two trips are deliberately not the same stay.

    The persistent opposite of a merge, so mergeable_trips stops re-proposing
    them on every load. Idempotent and order-independent: stored as a sorted
    pair, so a repeat -- or the same dismissal from the other trip's panel -- is
    a no-op.
    """
    low, high = sorted((trip_id_a, trip_id_b))
    existing = session.exec(
        select(MergeDismissal)
        .where(MergeDismissal.trip_low_id == low)
        .where(MergeDismissal.trip_high_id == high)
    ).first()
    if existing:
        return
    session.add(MergeDismissal(trip_low_id=low, trip_high_id=high))
    session.commit()


def mergeable_trips(session: Session, trip: Trip) -> list[dict]:
    """Other trips that are plausibly the same trip as this one.

    Same country, both dated, with spans overlapping or within a few weeks, and
    not already dismissed as deliberately separate. A hint for the UI, nothing
    more: merging is always a deliberate act.
    """
    if trip.start_date is None or trip.end_date is None:
        return []
    code = trip_country_code(session, trip.id)
    if code is None:
        return []

    dismissed = _dismissed_partner_ids(session, trip.id)
    others = session.exec(
        select(Trip)
        .where(Trip.id != trip.id)
        .where(Trip.start_date.is_not(None))
        .where(Trip.end_date.is_not(None))
    ).all()

    out: list[dict] = []
    for other in others:
        if other.id in dismissed:
            continue
        if trip_country_code(session, other.id) != code:
            continue
        # Positive gap when the spans are disjoint; <= 0 when they touch or
        # overlap. Only one of the two terms can be positive.
        gap = max(
            (other.start_date - trip.end_date).days,
            (trip.start_date - other.end_date).days,
        )
        if gap > MERGE_ADJACENCY_DAYS:
            continue
        stays = session.exec(select(Stay).where(Stay.trip_id == other.id)).all()
        out.append(
            {
                "id": other.id,
                "label": trip_label(stays),
                "start_date": str(other.start_date),
                "end_date": str(other.end_date),
            }
        )
    out.sort(key=lambda t: t["start_date"])
    return out


def merge_trips(session: Session, target: Trip, source: Trip) -> Trip:
    """Fold `source` into `target`, then delete `source`.

    Every hotel, journey, note and requirement moves to `target`, and the one
    country entry is kept (target's if it has one, else source's). The caller
    must already have checked the two are the same country -- this only moves
    rows. Derived state is rebuilt afterwards, exactly as any other edit does.
    """
    for model in (Stay, Leg, Requirement, Note):
        for row in session.exec(
            select(model).where(model.trip_id == source.id)
        ).all():
            row.trip_id = target.id
            session.add(row)

    # A trip has at most one country entry. Keep target's (it carries the
    # passport already chosen); otherwise adopt source's. Drop the rest so
    # sync_country_entries has a single row to normalise.
    target_entry = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == target.id)
    ).first()
    for entry in session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == source.id)
    ).all():
        if target_entry is None:
            entry.trip_id = target.id
            session.add(entry)
            target_entry = entry
        else:
            session.delete(entry)
    session.commit()

    # Reload source so its cascade-delete relationships see the now-empty
    # collections; otherwise deleting it could take the reassigned rows with it.
    session.expire(source)
    session.delete(source)
    session.commit()

    refresh_trip_dates(session, target)
    session.commit()
    sync_country_entries(session, target)
    session.refresh(target)
    return target


def trip_detail(session: Session, trip: Trip) -> dict:
    """Full trip payload: the shape the frontend's detail panel consumes."""
    stays = session.exec(
        select(Stay).where(Stay.trip_id == trip.id).order_by(Stay.check_in)
    ).all()
    notes = session.exec(
        select(Note).where(Note.trip_id == trip.id).order_by(Note.on_date)
    ).all()

    return {
        # `notes` is the trip's own memo. The Note records go under notes_list --
        # writing "notes" twice here silently replaced the memo with an array.
        **trip.model_dump(),
        "label": trip_label(stays),
        "status": trip_status(trip),
        "country": trip_country(session, trip),
        "nights": sum(s.nights for s in stays),
        "requirements": [r.model_dump() for r in trip.requirements],
        "notes_list": [n.model_dump() for n in notes],
        "mergeable": mergeable_trips(session, trip),
    }
