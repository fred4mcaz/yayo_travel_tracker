"""Passports.

Top-level rather than nested: a passport outlives any single trip, and the
per-country history ("when was I last in Japan, and on which passport") is a
query across trips.
"""

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api.common import apply_update, get_or_404
from app.db import get_session
from app.models import CountryEntry, Passport, Trip
from app.schemas import PassportCreate, PassportUpdate

router = APIRouter(prefix="/api/passports", tags=["passports"])


def _clear_other_defaults(session: Session, keep_id: int | None) -> None:
    for other in session.exec(select(Passport).where(Passport.is_default == True)).all():  # noqa: E712
        if other.id != keep_id:
            other.is_default = False
            session.add(other)


@router.get("")
def list_passports(session: Session = Depends(get_session)) -> list[dict]:
    passports = session.exec(select(Passport).order_by(Passport.nationality)).all()
    out = []
    for p in passports:
        used = session.exec(
            select(CountryEntry).where(CountryEntry.passport_id == p.id)
        ).all()
        out.append(
            {
                **p.model_dump(),
                "countries_entered": sorted({e.country_code for e in used}),
                "entry_count": len(used),
            }
        )
    return out


@router.post("", status_code=201)
def create_passport(
    payload: PassportCreate, session: Session = Depends(get_session)
) -> dict:
    passport = Passport(**payload.model_dump())
    session.add(passport)
    session.commit()
    session.refresh(passport)
    if passport.is_default:
        _clear_other_defaults(session, passport.id)
        session.commit()
        # commit() expires the instance; without this refresh model_dump()
        # returns an empty dict rather than the row that was just written.
        session.refresh(passport)
    return passport.model_dump()


@router.patch("/{passport_id}")
def update_passport(
    passport_id: int, payload: PassportUpdate, session: Session = Depends(get_session)
) -> dict:
    passport = get_or_404(session, Passport, passport_id, "passport")
    apply_update(passport, payload)
    session.add(passport)
    if passport.is_default:
        _clear_other_defaults(session, passport.id)
    session.commit()
    session.refresh(passport)
    return passport.model_dump()


@router.delete("/{passport_id}", status_code=204)
def delete_passport(passport_id: int, session: Session = Depends(get_session)) -> None:
    """Deleting a passport nulls it out on past entries rather than losing them.

    The trip history is the valuable record; which document you carried is an
    attribute of it. The ondelete="SET NULL" on CountryEntry.passport_id handles
    this at the database level.
    """
    passport = get_or_404(session, Passport, passport_id, "passport")
    session.delete(passport)
    session.commit()


@router.get("/history/{country_code}")
def country_history(
    country_code: str, session: Session = Depends(get_session)
) -> list[dict]:
    """Every recorded entry into one country, most recent first."""
    entries = session.exec(
        select(CountryEntry)
        .where(CountryEntry.country_code == country_code.upper())
        .order_by(CountryEntry.entered_on.desc())
    ).all()
    out = []
    for e in entries:
        trip = session.get(Trip, e.trip_id)
        out.append(
            {
                **e.model_dump(),
                "trip_title": trip.title if trip else None,
                "passport_nationality": (
                    e.passport.nationality.value if e.passport else None
                ),
            }
        )
    return out
