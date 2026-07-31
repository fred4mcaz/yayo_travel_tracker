"""Phase 3: extraction behind a strict tool schema.

No network. The model is injected behind a Protocol, and the one test that
exercises the real Anthropic client hands it a mock HTTP transport so the strict
tool schema is validated end to end without a socket.

The line under test: extractions land as pending proposals, and a malformed
model response is rejected rather than persisted. Nothing here touches trip
data -- that is phase 4.
"""

import json
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, select

from app.models import EmailMessage, Extraction, ExtractionStatus
from app.services.extraction import (
    EXTRACT_TOOL,
    TRIAGE_TOOL,
    AnthropicModel,
    Booking,
    TriageResult,
    process_email,
    run_extractions,
    validate_booking,
)


# --------------------------------------------------------------------------
# A fake model
# --------------------------------------------------------------------------


class FakeModel:
    """Canned triage and extraction, and a record of what it was asked."""

    def __init__(self, triage: TriageResult, extract=None):
        self._triage = triage
        self._extract = extract
        self.triaged: list[tuple[str, str]] = []
        self.extracted: list[tuple[str, str]] = []

    def triage(self, subject, body):
        self.triaged.append((subject, body))
        return self._triage

    def extract(self, subject, body):
        self.extracted.append((subject, body))
        return self._extract


def _candidate(session: Session, uid: int = 10, **kw) -> EmailMessage:
    email = EmailMessage(
        imap_uid=uid,
        message_id=f"<{uid}@mail.example>",
        from_addr=kw.get("from_addr", "no-reply@booking.com"),
        subject=kw.get("subject", "Your booking is confirmed"),
        received_at=kw.get("received_at", datetime(2026, 8, 1, 9, 0)),
        snippet=kw.get("snippet", "Sofitel Legend, Hanoi. Check-in 30 Aug 2026."),
        looks_like_travel=kw.get("looks_like_travel", True),
    )
    session.add(email)
    session.commit()
    session.refresh(email)
    return email


VALID_BOOKING = {
    "kind": "hotel",
    "country_code": "VN",
    "city": "Hanoi",
    "start_date": "2026-08-30",
    "end_date": "2026-09-03",
    "hotel_name": "Sofitel Legend Metropole",
    "carrier": None,
    "confirmation_code": "4471",
    "confidence": 0.94,
}


def _extractions(session: Session) -> list[Extraction]:
    return list(session.exec(select(Extraction)).all())


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_a_candidate_becomes_a_pending_proposal(session: Session):
    email = _candidate(session)
    model = FakeModel(TriageResult(True, 0.9, "looks like a booking"), VALID_BOOKING)

    result = run_extractions(session, model)

    assert result == {"processed": 1, "proposed": 1}
    rows = _extractions(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == ExtractionStatus.pending
    assert row.email_message_id == email.id
    assert row.confidence == 0.94
    payload = json.loads(row.payload_json)
    assert payload["country_code"] == "VN"
    assert payload["hotel_name"] == "Sofitel Legend Metropole"
    # Confidence is a column, not duplicated into the payload.
    assert "confidence" not in payload


def test_the_email_is_marked_processed_and_not_retried(session: Session):
    _candidate(session)
    model = FakeModel(TriageResult(True, 0.9, "yes"), VALID_BOOKING)

    run_extractions(session, model)
    again = run_extractions(session, model)

    assert again == {"processed": 0, "proposed": 0}
    assert len(_extractions(session)) == 1


# --------------------------------------------------------------------------
# The funnel: triage gates extraction
# --------------------------------------------------------------------------


def test_triage_no_means_the_extractor_is_never_called(session: Session):
    _candidate(session)
    model = FakeModel(TriageResult(False, 0.8, "just a newsletter"))

    result = run_extractions(session, model)

    assert result == {"processed": 1, "proposed": 0}
    assert model.extracted == []  # sonnet was never spent
    assert _extractions(session) == []


def test_only_travel_candidates_are_considered(session: Session):
    _candidate(session, uid=10, looks_like_travel=True)
    _candidate(session, uid=11, looks_like_travel=False, subject="lunch?")
    model = FakeModel(TriageResult(True, 0.9, "yes"), VALID_BOOKING)

    run_extractions(session, model)

    # The non-candidate was never even triaged.
    assert len(model.triaged) == 1
    assert len(_extractions(session)) == 1


# --------------------------------------------------------------------------
# Malformed extractions are rejected, not persisted
# --------------------------------------------------------------------------


def test_malformed_extraction_is_rejected_but_email_marked_processed(
    session: Session,
):
    email = _candidate(session)
    # Triage says yes, but the extractor returns junk.
    model = FakeModel(TriageResult(True, 0.9, "yes"), {"kind": "banana"})

    result = run_extractions(session, model)

    assert result == {"processed": 1, "proposed": 0}
    assert _extractions(session) == []
    session.refresh(email)
    assert email.processed_at is not None  # not retried forever


def test_extractor_returning_none_persists_nothing(session: Session):
    _candidate(session)
    model = FakeModel(TriageResult(True, 0.9, "yes"), None)

    result = run_extractions(session, model)

    assert result == {"processed": 1, "proposed": 0}
    assert _extractions(session) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "hotel", "country_code": "VNM", "start_date": "2026-08-30"},
        {"kind": "hotel", "country_code": "12", "start_date": "2026-08-30"},
        {"kind": "hotel", "country_code": "VN", "start_date": "2026-13-40"},
        {"kind": "hotel", "country_code": "VN", "start_date": "not a date"},
        {"kind": "hotel", "country_code": None, "start_date": None},
        {"kind": "hotel", "country_code": "VN", "confidence": 5},
        {"not": "a booking"},
        "a bare string",
        None,
    ],
)
def test_validate_booking_rejects_unusable_payloads(payload):
    assert validate_booking(payload) is None


def test_validate_booking_normalises_a_good_payload():
    booking = validate_booking(
        {
            "kind": "flight",
            "country_code": "th",
            "city": "  Bangkok  ",
            "start_date": "2026-09-03",
            "end_date": None,
            "hotel_name": None,
            "carrier": "Vietnam Airlines",
            "confirmation_code": " ABC123 ",
            "confidence": 0.8,
        }
    )
    assert isinstance(booking, Booking)
    assert booking.country_code == "TH"  # upper-cased
    assert booking.city == "Bangkok"  # trimmed
    assert booking.confirmation_code == "ABC123"
    assert booking.confidence == 0.8


def test_a_leg_with_a_date_but_no_country_is_kept():
    """Phase 4 can still match a leg by date; do not throw it away."""
    booking = validate_booking(
        {"kind": "flight", "country_code": None, "start_date": "2026-09-03"}
    )
    assert booking is not None
    assert booking.start_date == "2026-09-03"


def test_process_email_returns_the_extraction_or_none(session: Session):
    email = _candidate(session)
    yes = FakeModel(TriageResult(True, 0.9, "y"), VALID_BOOKING)
    assert process_email(session, yes, email) is not None

    email2 = _candidate(session, uid=11)
    no = FakeModel(TriageResult(False, 0.1, "n"))
    assert process_email(session, no, email2) is None


# --------------------------------------------------------------------------
# The real Anthropic client, against a mock transport -- exercises the strict
# tool schema without a network call
# --------------------------------------------------------------------------


def _anthropic_tool_response(tool_name: str, tool_input: dict) -> httpx.Response:
    """A Messages API response containing a single tool_use block."""
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_test",
                    "name": tool_name,
                    "input": tool_input,
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
    )


def _model_with_transport(handler) -> AnthropicModel:
    import anthropic

    client = anthropic.Anthropic(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return AnthropicModel(client)


def test_real_client_sends_strict_tools_and_reads_the_tool_use(session: Session):
    """The strict schema goes out on the wire; the tool input comes back."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.update(payload)
        name = payload["tool_choice"]["name"]
        if name == TRIAGE_TOOL["name"]:
            return _anthropic_tool_response(
                name, {"is_booking": True, "confidence": 0.9, "reason": "ok"}
            )
        return _anthropic_tool_response(name, VALID_BOOKING)

    model = _model_with_transport(handler)

    triage = model.triage("Your booking", "Sofitel, Hanoi")
    assert triage.is_booking is True
    # The tool that went out was declared strict.
    assert sent["tools"][0]["strict"] is True
    assert sent["tools"][0]["name"] == TRIAGE_TOOL["name"]

    booking = validate_booking(model.extract("Your booking", "Sofitel, Hanoi"))
    assert booking is not None
    assert booking.country_code == "VN"
    assert sent["tools"][0]["name"] == EXTRACT_TOOL["name"]


def test_real_client_end_to_end_creates_a_pending_extraction(session: Session):
    email = _candidate(session)

    def handler(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["tool_choice"]["name"]
        if name == TRIAGE_TOOL["name"]:
            return _anthropic_tool_response(
                name, {"is_booking": True, "confidence": 0.95, "reason": "booking"}
            )
        return _anthropic_tool_response(name, VALID_BOOKING)

    model = _model_with_transport(handler)

    result = run_extractions(session, model)

    assert result == {"processed": 1, "proposed": 1}
    rows = _extractions(session)
    assert len(rows) == 1
    assert rows[0].status == ExtractionStatus.pending
    assert rows[0].email_message_id == email.id
