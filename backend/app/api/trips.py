"""Trips.

A trip is one international stay in one country: the journey that got you
there, the passport you entered on, and every hotel you sleep in while there.

Hotels and travel are nested under their trip (/api/trips/{id}/stays) rather
than exposed as top-level collections: neither has meaning without its trip,
and nesting makes the ownership check structural instead of something each
handler has to remember.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.common import apply_update, get_child_or_404, get_or_404
from app.countries import country_name
from app.db import get_session
from app.models import CountryEntry, Leg, Requirement, Stay, Trip
from app.schemas import (
    CountryEntryUpdate,
    LegCreate,
    LegUpdate,
    RequirementCreate,
    RequirementUpdate,
    StayCreate,
    StayUpdate,
    TripCreate,
    TripUpdate,
)
from app.services.entry_policy import policy_model_or_none
from app.services.geocode import fill_coordinates
from app.services.trips import (
    keep_trips_separate,
    merge_trips,
    refresh_trip_dates,
    sync_country_entries,
    sync_requirements,
    trip_arrival_mode,
    trip_country,
    trip_country_code,
    trip_detail,
    trip_label,
    trip_readiness,
    trip_status,
)

router = APIRouter(prefix="/api/trips", tags=["trips"])


def _guard_single_country(session: Session, trip_id: int, code: str) -> None:
    """A trip is one country. Refuse anything that would make it two.

    Enforced here rather than left to the UI, so the invariant everything else
    depends on cannot be broken by a stray API call or an email extraction.
    """
    if not code:
        return
    existing = trip_country_code(session, trip_id)
    if existing and existing != code.upper():
        raise HTTPException(
            status_code=409,
            detail=(
                f"This trip is a stay in {country_name(existing)}. "
                f"Create a new trip for {country_name(code)}."
            ),
        )


# --------------------------------------------------------------------------
# Trips
# --------------------------------------------------------------------------


@router.get("")
def list_trips(session: Session = Depends(get_session)) -> list[dict]:
    """Most recent first. The frontend groups these into ongoing/upcoming/past."""
    out = []
    for trip in session.exec(select(Trip)).all():
        stays = session.exec(select(Stay).where(Stay.trip_id == trip.id)).all()
        country = trip_country(session, trip)
        out.append(
            {
                **trip.model_dump(),
                "label": trip_label(stays),
                "status": trip_status(trip),
                "country_code": country["country_code"] if country else "",
                "country_name": country["country_name"] if country else "",
                "cities": [s.city for s in sorted(stays, key=lambda s: s.check_in)],
                # The calendar draws one bar per hotel, so the list has to carry
                # them. Only what a bar needs: cost, address and confirmation
                # code have no business in a payload fetched on every page load.
                "stays": [
                    {
                        "id": s.id,
                        "city": s.city,
                        "hotel_name": s.hotel_name,
                        "check_in": str(s.check_in),
                        "check_out": str(s.check_out),
                        "nights": s.nights,
                    }
                    for s in sorted(stays, key=lambda s: s.check_in)
                ],
                "nights": sum(s.nights for s in stays),
                # The mode of the arrival leg, so the calendar can mark the gap
                # between this trip and the previous one with how you travelled
                # into it. None when nothing records how you got here.
                "arrival_mode": trip_arrival_mode(session, trip.id),
                # Surfaced on the card so a forgotten hotel is visible without
                # opening the trip -- the thing most worth noticing at a glance.
                "unbooked_nights": sum(
                    g["nights"] for g in (country["unbooked"] if country else [])
                ),
                # Compact readiness for the card badge -- the checklist detail
                # and advisory text only render on the trip's own detail panel.
                "readiness": {
                    k: v
                    for k, v in trip_readiness(session, trip).items()
                    if k in ("state", "permit", "permitted_days", "arrival_card", "checked_on")
                },
            }
        )
    # Undated trips sort last; otherwise most recent start first.
    out.sort(
        key=lambda t: (t["start_date"] is None, t["start_date"] or ""), reverse=True
    )
    return out


@router.post("", status_code=201)
def create_trip(payload: TripCreate, session: Session = Depends(get_session)) -> dict:
    trip = Trip(**payload.model_dump())
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip_detail(session, trip)


@router.get("/{trip_id}")
def get_trip(trip_id: int, session: Session = Depends(get_session)) -> dict:
    return trip_detail(session, get_or_404(session, Trip, trip_id, "trip"))


@router.patch("/{trip_id}")
def update_trip(
    trip_id: int, payload: TripUpdate, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    apply_update(trip, payload)
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip_detail(session, trip)


@router.delete("/{trip_id}", status_code=204)
def delete_trip(trip_id: int, session: Session = Depends(get_session)) -> None:
    trip = get_or_404(session, Trip, trip_id, "trip")
    session.delete(trip)
    session.commit()


class MergePayload(BaseModel):
    # The trip to fold into this one. It is deleted; this trip survives.
    other_trip_id: int


@router.post("/{trip_id}/merge")
def merge_trip(
    trip_id: int, payload: MergePayload, session: Session = Depends(get_session)
) -> dict:
    """Fold another trip into this one -- for when a single stay was split into
    two trips (a later hotel that landed outside the first trip's dates). This
    trip survives; the other is absorbed and deleted.

    Refused across countries: a trip is one country, so merging two different
    ones would break the invariant everything else depends on.
    """
    target = get_or_404(session, Trip, trip_id, "trip")
    if payload.other_trip_id == trip_id:
        raise HTTPException(status_code=400, detail="A trip cannot be merged into itself.")
    source = get_or_404(session, Trip, payload.other_trip_id, "trip")

    target_code = trip_country_code(session, trip_id)
    source_code = trip_country_code(session, source.id)
    if target_code and source_code and target_code != source_code:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This trip is a stay in {country_name(target_code)}, "
                f"the other in {country_name(source_code)}. "
                "Trips in different countries cannot be merged."
            ),
        )

    merge_trips(session, target, source)
    return trip_detail(session, target)


class KeepSeparatePayload(BaseModel):
    # The other half of a merge suggestion this trip should stop offering.
    other_trip_id: int


@router.post("/{trip_id}/keep-separate")
def keep_separate(
    trip_id: int,
    payload: KeepSeparatePayload,
    session: Session = Depends(get_session),
) -> dict:
    """Permanently dismiss the merge suggestion between these two trips.

    The deliberate opposite of a merge: it records that they are not the same
    stay, so mergeable_trips stops re-proposing them on every load. Symmetric --
    the suggestion clears from both trips' panels -- and idempotent.
    """
    trip = get_or_404(session, Trip, trip_id, "trip")
    if payload.other_trip_id == trip_id:
        raise HTTPException(
            status_code=400, detail="A trip cannot be kept separate from itself."
        )
    get_or_404(session, Trip, payload.other_trip_id, "trip")
    keep_trips_separate(session, trip_id, payload.other_trip_id)
    return trip_detail(session, trip)


def _after_change(session: Session, trip: Trip) -> dict:
    """Every hotel or travel change reshapes the span and the country entry."""
    refresh_trip_dates(session, trip)
    session.commit()
    sync_country_entries(session, trip)
    sync_requirements(session, trip, policy_model_or_none())
    session.refresh(trip)
    return trip_detail(session, trip)


# --------------------------------------------------------------------------
# Hotels
# --------------------------------------------------------------------------


@router.post("/{trip_id}/stays", status_code=201)
def create_stay(
    trip_id: int, payload: StayCreate, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    _guard_single_country(session, trip_id, payload.country_code)
    stay = Stay(trip_id=trip_id, **payload.model_dump())
    # The form only collects country and city, so derive the map pin here.
    fill_coordinates(stay)
    session.add(stay)
    session.commit()
    return _after_change(session, trip)


@router.patch("/{trip_id}/stays/{stay_id}")
def update_stay(
    trip_id: int,
    stay_id: int,
    payload: StayUpdate,
    session: Session = Depends(get_session),
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    stay = get_child_or_404(session, Stay, stay_id, trip_id, "stay")

    if payload.country_code:
        # Moving the trip's only hotel is just correcting which country the trip
        # is in; moving one of several would split the trip across two.
        others = [
            s
            for s in session.exec(select(Stay).where(Stay.trip_id == trip_id)).all()
            if s.id != stay_id
        ]
        if others and others[0].country_code.upper() != payload.country_code.upper():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This trip is a stay in {country_name(others[0].country_code)}. "
                    "Move this hotel by creating a new trip instead."
                ),
            )

    moved = (payload.city is not None and payload.city != stay.city) or (
        payload.country_code is not None and payload.country_code != stay.country_code
    )
    apply_update(stay, payload)
    if moved and payload.lat is None and payload.lon is None:
        # The place changed, so the old pin is wrong. Clear it and re-derive.
        stay.lat = stay.lon = None
        fill_coordinates(stay)
    session.add(stay)
    session.commit()
    return _after_change(session, trip)


@router.delete("/{trip_id}/stays/{stay_id}")
def delete_stay(
    trip_id: int, stay_id: int, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    stay = get_child_or_404(session, Stay, stay_id, trip_id, "stay")
    session.delete(stay)
    session.commit()
    return _after_change(session, trip)


# --------------------------------------------------------------------------
# Travel
# --------------------------------------------------------------------------


@router.post("/{trip_id}/legs", status_code=201)
def create_leg(
    trip_id: int, payload: LegCreate, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    _guard_single_country(session, trip_id, payload.country_code)
    data = payload.model_dump()
    # The journey belongs to the trip's country whether or not it was named.
    data["country_code"] = (
        data.get("country_code") or trip_country_code(session, trip_id) or ""
    )
    session.add(Leg(trip_id=trip_id, **data))
    session.commit()
    return _after_change(session, trip)


@router.patch("/{trip_id}/legs/{leg_id}")
def update_leg(
    trip_id: int,
    leg_id: int,
    payload: LegUpdate,
    session: Session = Depends(get_session),
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    leg = get_child_or_404(session, Leg, leg_id, trip_id, "leg")
    apply_update(leg, payload)
    session.add(leg)
    session.commit()
    return _after_change(session, trip)


@router.delete("/{trip_id}/legs/{leg_id}")
def delete_leg(
    trip_id: int, leg_id: int, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    leg = get_child_or_404(session, Leg, leg_id, trip_id, "leg")
    session.delete(leg)
    session.commit()
    return _after_change(session, trip)


# --------------------------------------------------------------------------
# Paperwork
# --------------------------------------------------------------------------


@router.post("/{trip_id}/requirements", status_code=201)
def create_requirement(
    trip_id: int, payload: RequirementCreate, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    session.add(Requirement(trip_id=trip_id, **payload.model_dump()))
    session.commit()
    session.refresh(trip)
    return trip_detail(session, trip)


@router.patch("/{trip_id}/requirements/{req_id}")
def update_requirement(
    trip_id: int,
    req_id: int,
    payload: RequirementUpdate,
    session: Session = Depends(get_session),
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    req = get_child_or_404(session, Requirement, req_id, trip_id, "requirement")
    apply_update(req, payload)
    session.add(req)
    session.commit()
    session.refresh(trip)
    return trip_detail(session, trip)


@router.delete("/{trip_id}/requirements/{req_id}")
def delete_requirement(
    trip_id: int, req_id: int, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    req = get_child_or_404(session, Requirement, req_id, trip_id, "requirement")
    session.delete(req)
    session.commit()
    session.refresh(trip)
    return trip_detail(session, trip)


# --------------------------------------------------------------------------
# Which passport you entered on
# --------------------------------------------------------------------------


@router.patch("/{trip_id}/entries/{entry_id}")
def update_entry(
    trip_id: int,
    entry_id: int,
    payload: CountryEntryUpdate,
    session: Session = Depends(get_session),
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    entry = get_child_or_404(session, CountryEntry, entry_id, trip_id, "entry")
    apply_update(entry, payload)
    session.add(entry)
    session.commit()
    # The leaving date extends the trip's span, so recompute it.
    refresh_trip_dates(session, trip)
    session.commit()
    # A passport change (or country_code, in principle) changes which policy
    # applies, so the checklist needs reconciling too.
    sync_requirements(session, trip, policy_model_or_none())
    session.refresh(trip)
    return trip_detail(session, trip)
