"""Phase 5: the review-queue HTTP surface.

Uses the authenticated `client` fixture (auth is exercised for real elsewhere).
The gating test uses `anon_client` to prove the queue is behind auth like every
other data route.
"""

import json
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.models import (
    EmailMessage,
    Extraction,
    ExtractionStatus,
    LearnedRule,
    Stay,
    Trip,
    utcnow,
)
from app.services.email_ingest import IncomingEmail
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


# --------------------------------------------------------------------------
# Phase 4: recent emails + manual extract -- the safety net for a missed
# message the automatic filter never flagged
# --------------------------------------------------------------------------


def _email(session: Session, *, uid=200, received_at=None, **kw) -> EmailMessage:
    email = EmailMessage(
        imap_uid=uid,
        message_id=f"<{uid}@mail.example>",
        from_addr=kw.get("from_addr", "someone@unlisted.example"),
        subject=kw.get("subject", "Your ticket is confirmed"),
        received_at=received_at if received_at is not None else utcnow(),
        snippet=kw.get("snippet", "PNR RB998877"),
        looks_like_travel=kw.get("looks_like_travel", False),
    )
    session.add(email)
    session.commit()
    session.refresh(email)
    return email


VALID_MANUAL_BOOKING = {
    "kind": "ferry",
    "country_code": "SG",
    "city": "Batam",
    "start_date": "2026-08-05",
    "end_date": None,
    "hotel_name": None,
    "carrier": "redBus",
    "confirmation_code": "RB998877",
    "confidence": 0.9,
}


class _FakeMailbox:
    """Stands in for `ImapMailbox` as a context manager."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetch_by_message_id(self, message_id):
        if self._body is None:
            return None
        return IncomingEmail(
            uid=999,
            message_id=message_id,
            from_addr="x",
            subject="x",
            received_at=None,
            body=self._body,
        )


class _FakeMailboxFactory:
    """Stands in for the `ImapMailbox` class -- `.from_settings()` only."""

    def __init__(self, body=None):
        self._body = body

    def from_settings(self):
        return _FakeMailbox(self._body)


class _FailingMailboxFactory:
    """A mailbox that can't even connect -- the IMAP-unconfigured case."""

    def from_settings(self):
        raise RuntimeError("IMAP credentials are not configured.")


class _FakeExtractionModel:
    extract_model = "anthropic/claude-sonnet-5"

    def __init__(self, result):
        self._result = result
        self.calls: list[tuple] = []

    def extract(self, subject, body, received_on=None):
        self.calls.append((subject, body, received_on))
        return self._result


class _FakeModelFactory:
    def __init__(self, model):
        self._model = model

    def from_settings(self):
        return self._model


def _with_openrouter_key(client_call):
    """Run one client call with an OpenRouter key configured, then restore."""
    from app.config import Settings, get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(
        openrouter_api_key="sk-or-test"
    )
    try:
        return client_call()
    finally:
        del app.dependency_overrides[get_settings]


# --------------------------------------------------------------------------
# GET /recent-emails
# --------------------------------------------------------------------------


def test_recent_emails_lists_last_n_days_newest_first(client, session: Session):
    _email(session, uid=1, received_at=utcnow() - timedelta(days=10))
    mid = _email(session, uid=2, received_at=utcnow() - timedelta(days=1))
    new = _email(session, uid=3, received_at=utcnow() - timedelta(hours=1))

    rows = client.get("/api/review/recent-emails?days=3").json()

    assert [r["id"] for r in rows] == [new.id, mid.id]


def test_recent_emails_defaults_to_a_three_day_window(client, session: Session):
    _email(session, uid=1, received_at=utcnow() - timedelta(days=5))
    recent = _email(session, uid=2, received_at=utcnow() - timedelta(hours=2))

    rows = client.get("/api/review/recent-emails").json()

    assert [r["id"] for r in rows] == [recent.id]


def test_recent_emails_flags_whether_a_pending_proposal_already_exists(
    client, session: Session
):
    email = _email(session, uid=1)

    before = client.get("/api/review/recent-emails").json()
    assert before[0]["has_pending"] is False

    session.add(
        Extraction(
            email_message_id=email.id,
            model="m",
            payload_json="{}",
            status=ExtractionStatus.pending,
        )
    )
    session.commit()

    after = client.get("/api/review/recent-emails").json()
    assert after[0]["has_pending"] is True


def test_recent_emails_includes_unflagged_messages(client, session: Session):
    """The whole point: a message the automatic filter never touched still
    shows up here, ready to be manually selected."""
    _email(session, uid=1, looks_like_travel=False)

    rows = client.get("/api/review/recent-emails").json()

    assert rows[0]["looks_like_travel"] is False


def test_recent_emails_is_behind_auth(anon_client):
    assert anon_client.get("/api/review/recent-emails").status_code == 401


# --------------------------------------------------------------------------
# POST /emails/{id}/extract
# --------------------------------------------------------------------------


def test_extract_email_bypasses_the_filter_and_creates_a_pending_proposal(
    client, session: Session, monkeypatch
):
    from app import api

    email = _email(
        session,
        uid=5,
        looks_like_travel=False,
        from_addr="ticketmaster@redbus.sg",
        subject="Your ferry ticket",
        snippet="PNR RB998877",
    )
    monkeypatch.setattr(api.review, "ImapMailbox", _FakeMailboxFactory(body=None))
    monkeypatch.setattr(
        api.review,
        "OpenRouterModel",
        _FakeModelFactory(_FakeExtractionModel(VALID_MANUAL_BOOKING)),
    )

    res = _with_openrouter_key(
        lambda: client.post(f"/api/review/emails/{email.id}/extract")
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "pending"
    assert body["booking"]["confirmation_code"] == "RB998877"
    session.refresh(email)
    assert email.processed_at is not None


def test_extract_email_uses_a_live_refetch_body_when_available(
    client, session: Session, monkeypatch
):
    from app import api

    email = _email(session, uid=6, snippet="stale truncated snippet")
    model = _FakeExtractionModel(VALID_MANUAL_BOOKING)
    monkeypatch.setattr(
        api.review, "ImapMailbox", _FakeMailboxFactory(body="the full live body")
    )
    monkeypatch.setattr(api.review, "OpenRouterModel", _FakeModelFactory(model))

    _with_openrouter_key(lambda: client.post(f"/api/review/emails/{email.id}/extract"))

    assert model.calls[0][1] == "the full live body"


def test_extract_email_falls_back_to_the_stored_snippet_when_refetch_fails(
    client, session: Session, monkeypatch
):
    from app import api

    email = _email(session, uid=7, snippet="the only body we have")
    model = _FakeExtractionModel(VALID_MANUAL_BOOKING)
    monkeypatch.setattr(api.review, "ImapMailbox", _FailingMailboxFactory())
    monkeypatch.setattr(api.review, "OpenRouterModel", _FakeModelFactory(model))

    res = _with_openrouter_key(
        lambda: client.post(f"/api/review/emails/{email.id}/extract")
    )

    assert res.status_code == 200
    assert model.calls[0][1] == "the only body we have"


def test_extract_email_returns_the_existing_pending_proposal_instead_of_duplicating(
    client, session: Session, monkeypatch
):
    from app import api

    email = _email(session, uid=8)
    existing = Extraction(
        email_message_id=email.id,
        model="m",
        payload_json=json.dumps(VALID_MANUAL_BOOKING),
        status=ExtractionStatus.pending,
    )
    session.add(existing)
    session.commit()
    session.refresh(existing)

    model = _FakeExtractionModel(VALID_MANUAL_BOOKING)
    monkeypatch.setattr(api.review, "OpenRouterModel", _FakeModelFactory(model))
    monkeypatch.setattr(api.review, "ImapMailbox", _FakeMailboxFactory(body=None))

    res = _with_openrouter_key(
        lambda: client.post(f"/api/review/emails/{email.id}/extract")
    )

    assert res.status_code == 200
    assert res.json()["id"] == existing.id
    assert model.calls == []  # the existing proposal was reused, not re-extracted


def test_extract_email_when_the_model_finds_nothing_is_422(
    client, session: Session, monkeypatch
):
    from app import api

    email = _email(session, uid=9)
    monkeypatch.setattr(
        api.review, "OpenRouterModel", _FakeModelFactory(_FakeExtractionModel(None))
    )
    monkeypatch.setattr(api.review, "ImapMailbox", _FakeMailboxFactory(body=None))

    res = _with_openrouter_key(
        lambda: client.post(f"/api/review/emails/{email.id}/extract")
    )

    assert res.status_code == 422


def test_extract_missing_email_is_404(client):
    assert client.post("/api/review/emails/999/extract").status_code == 404


def test_extract_without_openrouter_key_configured_is_409(client, session: Session):
    email = _email(session, uid=10)

    res = client.post(f"/api/review/emails/{email.id}/extract")

    assert res.status_code == 409
    assert "YAYO_OPENROUTER_API_KEY" in res.json()["detail"]


def test_extract_is_behind_auth(anon_client):
    assert anon_client.post("/api/review/emails/1/extract").status_code == 401


# --------------------------------------------------------------------------
# D3: accepting a manually-extracted proposal teaches the filter the sender
# --------------------------------------------------------------------------


def test_manual_extract_then_accept_learns_the_sender_domain(
    client, session: Session, monkeypatch
):
    from app import api

    email = _email(
        session,
        uid=11,
        from_addr="ticketmaster@redbus2.example",
        looks_like_travel=False,
    )
    monkeypatch.setattr(api.review, "ImapMailbox", _FakeMailboxFactory(body=None))
    monkeypatch.setattr(
        api.review,
        "OpenRouterModel",
        _FakeModelFactory(_FakeExtractionModel(VALID_MANUAL_BOOKING)),
    )

    extracted = _with_openrouter_key(
        lambda: client.post(f"/api/review/emails/{email.id}/extract")
    ).json()

    # Extracting alone teaches nothing -- only an accepted proposal does (D3).
    assert session.exec(select(LearnedRule)).all() == []

    accept_res = client.post(f"/api/review/{extracted['id']}/accept", json={})

    assert accept_res.status_code == 200
    learned = session.exec(select(LearnedRule)).all()
    assert [r.domain for r in learned] == ["redbus2.example"]
