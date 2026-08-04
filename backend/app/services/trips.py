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
    Actor,
    CountryEntry,
    Leg,
    MergeDismissal,
    Nationality,
    Note,
    Requirement,
    RequirementKind,
    RequirementStatus,
    Stay,
    Trip,
    utcnow,
)
from app.services.entry_policy import (
    EntryPolicyModel,
    cached_policy,
    get_policy,
    readiness_passport,
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


def trip_arrival_mode(session: Session, trip_id: int) -> Optional[str]:
    """How you got into the country: the mode of the earliest-arriving leg.

    Every Leg is an arrival, so the one that lands first is the journey that
    brought you in. None when no travel is recorded. The calendar uses this to
    label the gap between two consecutive trips with how you travelled into the
    next country.
    """
    legs = session.exec(select(Leg).where(Leg.trip_id == trip_id)).all()
    if not legs:
        return None
    dated = [leg for leg in legs if (leg.arrive_at or leg.depart_at) is not None]
    if dated:
        first = min(dated, key=lambda leg: leg.arrive_at or leg.depart_at)
        return first.mode.value
    # No times on any leg: nothing to order by, so any of them stands in.
    return legs[0].mode.value


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


# --------------------------------------------------------------------------
# Immigration readiness (Phase 2)
# --------------------------------------------------------------------------

# The RequirementKinds a policy reading can produce a row for. `custom` is
# always user-authored -- the policy never creates or retires one.
POLICY_REQUIREMENT_KINDS = (
    RequirementKind.visa,
    RequirementKind.entry_card,
    RequirementKind.eta,
    RequirementKind.insurance,
    RequirementKind.vaccination,
    RequirementKind.onward_ticket,
)

_KIND_LABELS = {
    RequirementKind.visa: "Visa",
    RequirementKind.entry_card: "Arrival card",
    RequirementKind.eta: "Electronic travel authorization",
    RequirementKind.insurance: "Travel insurance",
    RequirementKind.vaccination: "Vaccination",
    RequirementKind.onward_ticket: "Onward ticket",
}

_SETTLED_STATUSES = {RequirementStatus.approved, RequirementStatus.not_required}


def _trip_entry(session: Session, trip_id: int) -> Optional[CountryEntry]:
    return session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip_id)
    ).first()


def sync_requirements(
    session: Session,
    trip: Trip,
    model: Optional[EntryPolicyModel] = None,
) -> list[Requirement]:
    """Materialize/reconcile the trip's `system`-sourced Requirement rows.

    Reads the cached (or, if `model` is given, freshly fetched) EntryPolicy
    for this trip's country and passport, then creates a row for every kind
    the policy marks required and retires any it no longer does -- but only
    ever touches rows with source == system whose status is still todo. A
    system row the user advanced, or an email confirmed, is left exactly as
    is even if the policy would no longer create it: that is the traveller's
    own record, not something a reconciliation gets to overwrite. Idempotent
    -- calling it twice in a row changes nothing.

    Undated trips and trips with no recorded country get no rows at all
    (mirrors sync_country_entries, and keeps readiness quiet on a trip that
    is not real yet): any of this trip's own todo system rows are retired.
    """
    system_rows = {
        r.kind: r
        for r in session.exec(
            select(Requirement)
            .where(Requirement.trip_id == trip.id)
            .where(Requirement.source == Actor.system)
        ).all()
    }

    code = trip_country_code(session, trip.id)
    if code is None or trip.start_date is None:
        for row in system_rows.values():
            if row.status == RequirementStatus.todo:
                session.delete(row)
        session.commit()
        return []

    entry = _trip_entry(session, trip.id)
    nationality = readiness_passport(entry)
    policy = get_policy(session, code, nationality, model)

    # An unknown policy (no cache row, no model to fetch one) means "we don't
    # know yet" -- not "nothing is required". Leave every existing row alone
    # rather than reading silence as a green light.
    if policy is None:
        session.commit()
        return list(system_rows.values())

    required_kinds = {
        kind
        for kind in POLICY_REQUIREMENT_KINDS
        if getattr(policy, f"{kind.value}_required")
    }

    for kind in POLICY_REQUIREMENT_KINDS:
        existing = system_rows.get(kind)
        if kind in required_kinds:
            if existing is None:
                session.add(
                    Requirement(
                        trip_id=trip.id,
                        kind=kind,
                        label=_KIND_LABELS[kind],
                        country_code=code,
                        source=Actor.system,
                    )
                )
        elif existing is not None and existing.status == RequirementStatus.todo:
            session.delete(existing)

    session.commit()
    return session.exec(
        select(Requirement)
        .where(Requirement.trip_id == trip.id)
        .where(Requirement.source == Actor.system)
    ).all()


def _other_nationality(nationality: Nationality) -> Nationality:
    return Nationality.MX if nationality == Nationality.US else Nationality.US


def _alternate_passport_hint(
    session: Session, code: str, nationality: Nationality, policy
) -> Optional[str]:
    """"The other passport would clear more easily" -- cache-only.

    Never triggers a model call: it only speaks up when the alternate
    (country, nationality) pair happens to already be cached, e.g. because
    another trip asked about it. Silent otherwise, including on the very
    first look at a country -- that first look isn't worth a second policy
    fetch just to maybe show a hint.
    """
    if not (policy.visa_required or policy.entry_card_required):
        return None
    other = _other_nationality(nationality)
    alt = cached_policy(session, code, other)
    if alt is None or alt.visa_required or alt.entry_card_required:
        return None
    return f"A {other.value} passport would be visa-free here."


def _discrepancy(
    session: Session, trip_id: int, nationality: Nationality
) -> Optional[dict]:
    """A loud passport-mismatch flag (decision 3): a Requirement row whose
    accepted immigration email (Phase 5) named a different nationality than
    the trip's *currently* selected passport.

    Checked independent of whether a policy is cached -- an accepted
    confirmation is real regardless of readiness state, and flipping the
    trip's passport later to match clears this without the stored row ever
    being touched again (see Requirement.discrepancy_nationality).
    """
    rows = session.exec(
        select(Requirement)
        .where(Requirement.trip_id == trip_id)
        .where(Requirement.discrepancy_nationality.is_not(None))
    ).all()
    for row in rows:
        if row.discrepancy_nationality != nationality:
            return {
                "kind": row.kind.value,
                "document_nationality": row.discrepancy_nationality.value,
                "selected_passport": nationality.value,
            }
    return None


def _empty_readiness(
    state: str,
    passport: Optional[str] = None,
    is_default_us: bool = False,
    discrepancy: Optional[dict] = None,
) -> dict:
    return {
        "state": state,
        "passport": passport,
        "is_default_us": is_default_us,
        "permit": None,
        "permitted_days": None,
        "checklist": [],
        "arrival_card": None,
        "advisory": "",
        "checked_on": None,
        "alternate_passport_hint": None,
        "discrepancy": discrepancy,
    }


def trip_readiness(session: Session, trip: Trip) -> dict:
    """Whether this trip is ready to cross the border, and what's left.

    state is one of:
    - na: no country recorded yet, or undated -- nothing to assess.
    - unknown: dated with a country, but no cached policy and nothing
      configured to fetch one -- an unconfigured box, not an error.
    - action: the policy is known and at least one required checklist item
      isn't approved / not_required yet.
    - ready: the policy is known and everything required is settled (or
      nothing is required at all, e.g. a visa-free trip with no arrival card).
    """
    code = trip_country_code(session, trip.id)
    if code is None or trip.start_date is None:
        return _empty_readiness("na")

    entry = _trip_entry(session, trip.id)
    nationality = readiness_passport(entry)
    is_default_us = entry is None or entry.passport is None
    discrepancy = _discrepancy(session, trip.id, nationality)

    policy = cached_policy(session, code, nationality)
    if policy is None:
        return _empty_readiness("unknown", nationality.value, is_default_us, discrepancy)

    rows = {
        r.kind: r
        for r in session.exec(
            select(Requirement)
            .where(Requirement.trip_id == trip.id)
            .where(Requirement.kind.in_(POLICY_REQUIREMENT_KINDS))
        ).all()
    }
    checklist = [
        {
            "kind": kind.value,
            "label": rows[kind].label or _KIND_LABELS[kind],
            "status": rows[kind].status.value,
        }
        for kind in POLICY_REQUIREMENT_KINDS
        if kind in rows
    ]
    ready = all(RequirementStatus(row["status"]) in _SETTLED_STATUSES for row in checklist)

    entry_card_row = rows.get(RequirementKind.entry_card)
    arrival_card = (
        {
            "name": policy.entry_card_name,
            "status": entry_card_row.status.value,
            "confirmed": entry_card_row.status == RequirementStatus.approved,
            "reference": entry_card_row.reference,
        }
        if entry_card_row is not None
        else None
    )

    return {
        "state": "ready" if ready else "action",
        "passport": nationality.value,
        "is_default_us": is_default_us,
        "permit": policy.permit_type.value if policy.permit_type else None,
        "permitted_days": policy.permitted_days,
        "checklist": checklist,
        "arrival_card": arrival_card,
        "advisory": policy.advisory,
        "checked_on": str(policy.fetched_at.date()),
        "alternate_passport_hint": _alternate_passport_hint(session, code, nationality, policy),
        "discrepancy": discrepancy,
    }


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
        "readiness": trip_readiness(session, trip),
    }
