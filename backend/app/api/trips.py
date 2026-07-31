"""Trips and their segments.

Segments are nested under their trip (/api/trips/{id}/stays) rather than exposed
as top-level collections: a stay has no meaning without its trip, and nesting
makes the ownership check structural instead of something each handler has to
remember.
"""

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.api.common import apply_update, get_child_or_404, get_or_404
from app.db import get_session
from app.models import CountryEntry, Leg, Requirement, Stay, Trip
from app.schemas import (
    CountryEntryCreate,
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
from app.services.geocode import fill_coordinates
from app.services.trips import (
    refresh_trip_dates,
    sync_country_entries,
    trip_detail,
    trip_status,
)

router = APIRouter(prefix="/api/trips", tags=["trips"])


# --------------------------------------------------------------------------
# Trips
# --------------------------------------------------------------------------


@router.get("")
def list_trips(
    session: Session = Depends(get_session),
    include_archived: bool = Query(default=False),
) -> list[dict]:
    """Newest first. The frontend groups these into ongoing/upcoming/past."""
    stmt = select(Trip)
    if not include_archived:
        stmt = stmt.where(Trip.archived == False)  # noqa: E712
    trips = session.exec(stmt).all()

    out = []
    for trip in trips:
        stays = session.exec(select(Stay).where(Stay.trip_id == trip.id)).all()
        out.append(
            {
                **trip.model_dump(),
                "status": trip_status(trip),
                "countries": sorted({s.country_code for s in stays}),
                "cities": [s.city for s in sorted(stays, key=lambda s: s.check_in)],
                "nights": sum(s.nights for s in stays),
            }
        )
    # Undated trips sort last; otherwise most recent start first.
    out.sort(key=lambda t: (t["start_date"] is None, t["start_date"] or ""), reverse=True)
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
    trip = get_or_404(session, Trip, trip_id, "trip")
    return trip_detail(session, trip)


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


# --------------------------------------------------------------------------
# Stays
# --------------------------------------------------------------------------


def _after_segment_change(session: Session, trip: Trip) -> dict:
    """Every segment mutation reshapes the trip span and its country entries."""
    refresh_trip_dates(session, trip)
    session.commit()
    sync_country_entries(session, trip)
    session.refresh(trip)
    return trip_detail(session, trip)


@router.post("/{trip_id}/stays", status_code=201)
def create_stay(
    trip_id: int, payload: StayCreate, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    stay = Stay(trip_id=trip_id, **payload.model_dump())
    # The form only collects country and city, so derive the pin here.
    fill_coordinates(stay)
    session.add(stay)
    session.commit()
    return _after_segment_change(session, trip)


@router.patch("/{trip_id}/stays/{stay_id}")
def update_stay(
    trip_id: int,
    stay_id: int,
    payload: StayUpdate,
    session: Session = Depends(get_session),
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    stay = get_child_or_404(session, Stay, stay_id, trip_id, "stay")
    moved = (
        payload.city is not None and payload.city != stay.city
    ) or (payload.country_code is not None and payload.country_code != stay.country_code)
    apply_update(stay, payload)
    if moved and payload.lat is None and payload.lon is None:
        # The place changed, so the old pin is wrong. Clear it and re-derive.
        stay.lat = stay.lon = None
        fill_coordinates(stay)
    session.add(stay)
    session.commit()
    return _after_segment_change(session, trip)


@router.delete("/{trip_id}/stays/{stay_id}")
def delete_stay(
    trip_id: int, stay_id: int, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    stay = get_child_or_404(session, Stay, stay_id, trip_id, "stay")
    session.delete(stay)
    session.commit()
    return _after_segment_change(session, trip)


# --------------------------------------------------------------------------
# Legs
# --------------------------------------------------------------------------


@router.post("/{trip_id}/legs", status_code=201)
def create_leg(
    trip_id: int, payload: LegCreate, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    session.add(Leg(trip_id=trip_id, **payload.model_dump()))
    session.commit()
    return _after_segment_change(session, trip)


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
    return _after_segment_change(session, trip)


@router.delete("/{trip_id}/legs/{leg_id}")
def delete_leg(
    trip_id: int, leg_id: int, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    leg = get_child_or_404(session, Leg, leg_id, trip_id, "leg")
    session.delete(leg)
    session.commit()
    return _after_segment_change(session, trip)


# --------------------------------------------------------------------------
# Requirements
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
# Country entries
#
# Rows are created automatically by sync_country_entries when stays cross a
# border. These endpoints exist to record which passport was used and to correct
# details the app cannot infer, such as the port of entry.
# --------------------------------------------------------------------------


@router.post("/{trip_id}/entries", status_code=201)
def create_entry(
    trip_id: int, payload: CountryEntryCreate, session: Session = Depends(get_session)
) -> dict:
    trip = get_or_404(session, Trip, trip_id, "trip")
    session.add(CountryEntry(trip_id=trip_id, **payload.model_dump()))
    session.commit()
    session.refresh(trip)
    return trip_detail(session, trip)


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
    session.refresh(trip)
    return trip_detail(session, trip)
