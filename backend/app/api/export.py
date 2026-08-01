"""Data export -- take everything with you.

Two shapes of the same data:

- **JSON** (`/trips.json`): the complete, faithful dump -- every trip with its
  hotels, travel, paperwork, passport entry and notes nested inside, plus
  standalone notes and passports. This is the "restore from this" copy.
- **CSV** (`/trips.zip`): a spreadsheet. CSV is flat and the data is three
  related tables, so it ships as a zip of `trips.csv`, `hotels.csv` and
  `legs.csv` rather than one confusing mega-row.

Both are behind the same auth as the rest of the data routes, and both set a
Content-Disposition so the browser downloads rather than renders them.
"""

import csv
import io
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlmodel import Session, select

from app.db import get_session
from app.models import Leg, Note, Passport, Stay, Trip
from app.services.trips import (
    trip_country,
    trip_detail,
    trip_label,
    trip_status,
)

router = APIRouter(prefix="/api/export", tags=["export"])


def _attachment(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


def _filename(ext: str) -> str:
    return f"yayo-travel-{datetime.now().strftime('%Y%m%d')}.{ext}"


@router.get("/trips.json")
def export_json(session: Session = Depends(get_session)) -> Response:
    """The complete nested export -- the same detail the app itself consumes."""
    trips = session.exec(select(Trip)).all()
    loose_notes = session.exec(select(Note).where(Note.trip_id.is_(None))).all()
    passports = session.exec(select(Passport)).all()

    payload = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "trips": [trip_detail(session, t) for t in trips],
        "notes": [n.model_dump() for n in loose_notes],
        "passports": [p.model_dump() for p in passports],
    }
    return JSONResponse(
        content=jsonable_encoder(payload),
        headers=_attachment(_filename("json")),
    )


TRIP_COLUMNS = [
    "trip_id", "label", "country_code", "country_name", "status",
    "start_date", "end_date", "nights", "unbooked_nights", "notes",
]
HOTEL_COLUMNS = [
    "trip_id", "trip_label", "country_code", "city", "hotel_name",
    "check_in", "check_out", "nights", "confirmation_code", "booking_source",
    "address", "cost", "currency", "notes",
]
LEG_COLUMNS = [
    "trip_id", "trip_label", "mode", "country_code", "carrier", "number",
    "from_place", "from_iata", "depart_at", "to_place", "to_iata", "arrive_at",
    "confirmation_code", "seat", "cost", "currency", "notes",
]


def _csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # None renders as the string "None" through csv; empty is what a
        # spreadsheet expects for a blank cell.
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buf.getvalue().encode("utf-8")


@router.get("/trips.zip")
def export_csv(session: Session = Depends(get_session)) -> Response:
    """A zip of trips.csv, hotels.csv and legs.csv -- the spreadsheet view."""
    trips = session.exec(select(Trip)).all()
    trip_rows: list[dict] = []
    hotel_rows: list[dict] = []
    leg_rows: list[dict] = []

    for trip in trips:
        stays = session.exec(
            select(Stay).where(Stay.trip_id == trip.id).order_by(Stay.check_in)
        ).all()
        legs = session.exec(
            select(Leg).where(Leg.trip_id == trip.id).order_by(Leg.depart_at)
        ).all()
        label = trip_label(stays)
        country = trip_country(session, trip)
        code = country["country_code"] if country else ""
        name = country["country_name"] if country else ""
        unbooked = sum(g["nights"] for g in country["unbooked"]) if country else 0

        trip_rows.append({
            "trip_id": trip.id,
            "label": label,
            "country_code": code,
            "country_name": name,
            "status": trip_status(trip),
            "start_date": trip.start_date,
            "end_date": trip.end_date,
            "nights": sum(s.nights for s in stays),
            "unbooked_nights": unbooked,
            "notes": trip.notes,
        })
        for s in stays:
            hotel_rows.append({
                "trip_id": trip.id,
                "trip_label": label,
                "country_code": s.country_code,
                "city": s.city,
                "hotel_name": s.hotel_name,
                "check_in": s.check_in,
                "check_out": s.check_out,
                "nights": s.nights,
                "confirmation_code": s.confirmation_code,
                "booking_source": s.booking_source,
                "address": s.address,
                "cost": s.cost,
                "currency": s.currency,
                "notes": s.notes,
            })
        for leg in legs:
            leg_rows.append({
                "trip_id": trip.id,
                "trip_label": label,
                "mode": leg.mode.value,
                "country_code": leg.country_code,
                "carrier": leg.carrier,
                "number": leg.number,
                "from_place": leg.from_place,
                "from_iata": leg.from_iata,
                "depart_at": leg.depart_at,
                "to_place": leg.to_place,
                "to_iata": leg.to_iata,
                "arrive_at": leg.arrive_at,
                "confirmation_code": leg.confirmation_code,
                "seat": leg.seat,
                "cost": leg.cost,
                "currency": leg.currency,
                "notes": leg.notes,
            })

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("trips.csv", _csv_bytes(trip_rows, TRIP_COLUMNS))
        archive.writestr("hotels.csv", _csv_bytes(hotel_rows, HOTEL_COLUMNS))
        archive.writestr("legs.csv", _csv_bytes(leg_rows, LEG_COLUMNS))

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers=_attachment(_filename("zip")),
    )
