"""Export: take your data with you, as JSON or a zip of CSVs.

Gating is proven with anon_client; the content tests use the authenticated
client and seed a trip with a hotel and a flight directly.
"""

import csv
import io
import json
import zipfile
from datetime import date, datetime

from sqlmodel import Session

from app.models import Leg, Stay, TravelMode, Trip
from app.services.trips import refresh_trip_dates, sync_country_entries


def _seed_trip(session: Session) -> Trip:
    trip = Trip(notes="the big one")
    session.add(trip)
    session.commit()
    session.refresh(trip)
    session.add(
        Stay(
            trip_id=trip.id,
            country_code="VN",
            city="Hanoi",
            hotel_name="Sofitel Legend",
            confirmation_code="SOF-1",
            check_in=date(2026, 8, 30),
            check_out=date(2026, 9, 2),
        )
    )
    session.add(
        Leg(
            trip_id=trip.id,
            mode=TravelMode.flight,
            country_code="VN",
            carrier="Vietnam Airlines",
            number="VN611",
            depart_at=datetime(2026, 8, 30, 14, 5),
        )
    )
    session.commit()
    refresh_trip_dates(session, trip)
    session.commit()
    sync_country_entries(session, trip)
    session.refresh(trip)
    return trip


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------


def test_export_is_behind_auth(anon_client):
    assert anon_client.get("/api/export/trips.json").status_code == 401
    assert anon_client.get("/api/export/trips.zip").status_code == 401


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def test_json_export_is_a_complete_nested_dump(client, session: Session):
    _seed_trip(session)

    res = client.get("/api/export/trips.json")

    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    assert res.headers["content-disposition"].endswith('.json"')

    data = json.loads(res.content)
    assert "exported_at" in data
    assert len(data["trips"]) == 1
    trip = data["trips"][0]
    # Nested hotels and travel are present, not just the trip shell.
    assert trip["country"]["country_code"] == "VN"
    assert trip["country"]["stays"][0]["hotel_name"] == "Sofitel Legend"
    assert trip["country"]["legs"][0]["carrier"] == "Vietnam Airlines"


def test_json_export_is_empty_but_valid_with_no_trips(client):
    res = client.get("/api/export/trips.json")

    assert res.status_code == 200
    data = json.loads(res.content)
    assert data["trips"] == []


# --------------------------------------------------------------------------
# CSV (zip)
# --------------------------------------------------------------------------


def _read_zip(content: bytes) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            text = archive.read(name).decode("utf-8")
            out[name] = list(csv.DictReader(io.StringIO(text)))
    return out


def test_csv_export_is_a_zip_of_three_tables(client, session: Session):
    _seed_trip(session)

    res = client.get("/api/export/trips.zip")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "attachment" in res.headers["content-disposition"]

    tables = _read_zip(res.content)
    assert set(tables) == {"trips.csv", "hotels.csv", "legs.csv"}

    assert len(tables["trips.csv"]) == 1
    assert tables["trips.csv"][0]["country_name"] == "Vietnam"
    assert tables["trips.csv"][0]["label"] == "Hanoi · Sofitel Legend"

    assert len(tables["hotels.csv"]) == 1
    assert tables["hotels.csv"][0]["hotel_name"] == "Sofitel Legend"
    assert tables["hotels.csv"][0]["nights"] == "3"

    assert len(tables["legs.csv"]) == 1
    assert tables["legs.csv"][0]["carrier"] == "Vietnam Airlines"
    assert tables["legs.csv"][0]["mode"] == "flight"


def test_csv_blank_cells_are_empty_not_the_string_none(client, session: Session):
    """A missing cost must be an empty cell, not the literal 'None'."""
    _seed_trip(session)

    tables = _read_zip(client.get("/api/export/trips.zip").content)

    # The seeded hotel has no cost and no address.
    assert tables["hotels.csv"][0]["cost"] == ""
    assert tables["hotels.csv"][0]["address"] == ""


def test_csv_export_has_headers_even_with_no_trips(client):
    tables = _read_zip(client.get("/api/export/trips.zip").content)

    # Empty of rows, but the files and their headers exist.
    assert tables["trips.csv"] == []
    assert tables["hotels.csv"] == []
    assert tables["legs.csv"] == []
    with zipfile.ZipFile(io.BytesIO(client.get("/api/export/trips.zip").content)) as z:
        header = z.read("hotels.csv").decode("utf-8").splitlines()[0]
    assert "hotel_name" in header
