"""Phase 5: the review-queue HTTP surface.

Uses the authenticated `client` fixture (auth is exercised for real elsewhere).
The gating test uses `anon_client` to prove the queue is behind auth like every
other data route.
"""

import json
from datetime import date, datetime

from sqlmodel import Session, select

from app.models import (
    EmailMessage,
    Extraction,
    ExtractionStatus,
    Stay,
    Trip,
)
from app.services.trips import refresh_trip_dates, sync_country_entries


def _seed_extraction(session: Session, *, suggested_trip_id=None, **booking) -> Extraction:
    payload = {
        "kind": booking.get("kind", "hotel"),
        "country_code": booking.get("country_code", "VN"),
        "city": booking.get("city", "Hanoi"),
        "start_date": booking.get("start_date", "2026-08-30"),
        "end_date": booking.get("end_date", "2026-09-03"),
        "hotel_name": booking.get("hotel_name", "Sofitel Legend"),
        "carrier": booking.get("carrier"),
        "confirmation_code": booking.get("confirmation_code", "4471"),
    }
    email = EmailMessage(
        imap_uid=booking.get("uid", 100),
        message_id=f"<{booking.get('uid', 100)}@mail.example>",
        from_addr="no-reply@booking.com",
        subject="Your booking is confirmed",
        snippet="Sofitel Legend, Hanoi",
        received_at=datetime(2026, 8, 1, 9, 0),
        looks_like_travel=True,
    )
    session.add(email)
    session.commit()
    session.refresh(email)

    row = Extraction(
        email_message_id=email.id,
        model="claude-sonnet-5",
        payload_json=json.dumps(payload, sort_keys=True),
        confidence=0.9,
        status=ExtractionStatus.pending,
        suggested_trip_id=suggested_trip_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _make_trip(session: Session, country: str, city: str, ci: str, co: str) -> Trip:
    trip = Trip(notes="")
    session.add(trip)
    session.commit()
    session.refresh(trip)
    session.add(
        Stay(
            trip_id=trip.id,
            country_code=country,
            city=city,
            check_in=date.fromisoformat(ci),
            check_out=date.fromisoformat(co),
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


def test_review_is_behind_auth(anon_client):
    assert anon_client.get("/api/review").status_code == 401
    assert anon_client.post("/api/review/1/accept", json={}).status_code == 401


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def test_list_shows_pending_with_email_and_booking(client, session: Session):
    _seed_extraction(session, country_code="VN", city="Hanoi")

    rows = client.get("/api/review").json()

    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "pending"
    assert row["email"]["subject"] == "Your booking is confirmed"
    assert row["booking"]["country_code"] == "VN"
    assert row["booking"]["country_name"] == "Vietnam"
    assert row["suggestion"] is None


def test_list_freshens_the_suggestion_against_current_trips(client, session: Session):
    """A trip that exists at read time should be suggested even if it did not
    when the extraction was created."""
    _seed_extraction(session, country_code="VN", start_date="2026-08-31")
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")

    rows = client.get("/api/review").json()

    assert rows[0]["suggestion"]["trip_id"] == trip.id
    assert "Hanoi" in rows[0]["suggestion"]["label"]


def test_count_endpoint(client, session: Session):
    _seed_extraction(session, uid=1)
    _seed_extraction(session, uid=2)

    assert client.get("/api/review/count").json() == {"pending": 2}


# --------------------------------------------------------------------------
# Accept
# --------------------------------------------------------------------------


def test_accept_creates_a_trip_and_marks_reviewed(client, session: Session):
    ext = _seed_extraction(session, country_code="JP", city="Osaka",
                           start_date="2026-10-01", end_date="2026-10-05")

    body = client.post(f"/api/review/{ext.id}/accept", json={}).json()

    assert body["accepted"] is True
    assert body["created_new_trip"] is True
    trips = session.exec(select(Trip)).all()
    assert len(trips) == 1
    session.refresh(ext)
    assert ext.status == ExtractionStatus.accepted


def test_accept_with_overrides_fixes_a_missing_field(client, session: Session):
    # The model missed the city; the reviewer supplies it.
    ext = _seed_extraction(session, country_code="TH", city=None,
                           start_date="2026-09-01", end_date="2026-09-04")

    body = client.post(
        f"/api/review/{ext.id}/accept", json={"city": "Bangkok"}
    ).json()

    assert body["accepted"] is True
    stay = session.exec(select(Stay)).first()
    assert stay.city == "Bangkok"


def test_accept_an_incomplete_hotel_without_a_fix_is_422(client, session: Session):
    ext = _seed_extraction(session, country_code="TH", city=None,
                           start_date="2026-09-01", end_date="2026-09-04")

    res = client.post(f"/api/review/{ext.id}/accept", json={})

    assert res.status_code == 422
    assert "city" in res.json()["detail"]
    # Nothing was created.
    assert session.exec(select(Trip)).all() == []


def test_accept_attaches_to_the_suggested_trip(client, session: Session):
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    ext = _seed_extraction(session, suggested_trip_id=trip.id, country_code="VN",
                           city="Hue", start_date="2026-09-01", end_date="2026-09-02")

    body = client.post(f"/api/review/{ext.id}/accept", json={}).json()

    assert body["trip_id"] == trip.id
    assert body["created_new_trip"] is False
    assert len(session.exec(select(Stay)).all()) == 2


def test_accepting_a_missing_extraction_is_404(client):
    assert client.post("/api/review/999/accept", json={}).status_code == 404


def test_overrides_cannot_smuggle_a_trip_id(client, session: Session):
    """The accept body only carries booking fields; a stray trip_id is ignored,
    not honoured."""
    ext = _seed_extraction(session, country_code="JP", city="Kyoto",
                           start_date="2026-10-01", end_date="2026-10-03")

    body = client.post(
        f"/api/review/{ext.id}/accept",
        json={"suggested_trip_id": 999, "trip_id": 999},
    ).json()

    # A fresh trip was created, not trip 999.
    assert body["created_new_trip"] is True
    assert body["trip_id"] != 999


# --------------------------------------------------------------------------
# Reject
# --------------------------------------------------------------------------


def test_reject_marks_rejected_and_writes_no_trip_data(client, session: Session):
    ext = _seed_extraction(session, country_code="JP", city="Nara")

    body = client.post(f"/api/review/{ext.id}/reject").json()

    assert body == {"rejected": True}
    session.refresh(ext)
    assert ext.status == ExtractionStatus.rejected
    assert session.exec(select(Trip)).all() == []


def test_rejected_proposal_leaves_the_pending_list(client, session: Session):
    ext = _seed_extraction(session, country_code="JP", city="Nara")
    client.post(f"/api/review/{ext.id}/reject")

    assert client.get("/api/review").json() == []
    assert client.get("/api/review/count").json() == {"pending": 0}
    # But it is still visible in the history.
    history = client.get("/api/review?include_reviewed=true").json()
    assert len(history) == 1
    assert history[0]["status"] == "rejected"


# --------------------------------------------------------------------------
# Manual poll -- gated exactly like the scheduler
# --------------------------------------------------------------------------


def test_poll_when_disabled_is_409_and_runs_nothing(client):
    """The default: ingest off. The endpoint says so rather than pretending."""
    res = client.post("/api/review/poll")
    assert res.status_code == 409
    assert "YAYO_EMAIL_INGEST_ENABLED" in res.json()["detail"]


def test_poll_enabled_but_unconfigured_is_409_naming_the_gaps(client):
    from app.config import Settings, get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(
        email_ingest_enabled=True
    )
    try:
        res = client.post("/api/review/poll")
    finally:
        del app.dependency_overrides[get_settings]

    assert res.status_code == 409
    assert "YAYO_IMAP_USER" in res.json()["detail"]


def test_poll_when_configured_runs_a_cycle(client, monkeypatch):
    from app.config import Settings, get_settings
    from app import api
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(
        email_ingest_enabled=True,
        imap_user="e@gmail.com",
        imap_app_password="pw",
        openrouter_api_key="sk-or",
    )
    # Stub the actual cycle: this test is about the endpoint wiring, not IMAP.
    monkeypatch.setattr(
        api.review,
        "run_poll_cycle",
        lambda engine: {"ingest": {"ingested": 2}, "extraction": {"proposed": 1}},
    )
    try:
        res = client.post("/api/review/poll")
    finally:
        del app.dependency_overrides[get_settings]

    assert res.status_code == 200
    body = res.json()
    assert body["polled"] is True
    assert body["ingest"]["ingested"] == 2
    assert body["extraction"]["proposed"] == 1


def test_poll_is_behind_auth(anon_client):
    assert anon_client.post("/api/review/poll").status_code == 401
