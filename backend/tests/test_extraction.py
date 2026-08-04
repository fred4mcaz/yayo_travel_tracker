"""Phase 3: extraction behind a strict tool schema.

No network. The model is injected behind a Protocol, and the one test that
exercises the real OpenRouter client hands it a mock HTTP transport so the
strict tool schema is validated end to end without a socket.

The line under test: extractions land as pending proposals, and a malformed
model response is rejected rather than persisted. Nothing here touches trip
data -- that is phase 4.
"""

import json
from datetime import date, datetime

import httpx
import pytest
from sqlmodel import Session, select

from app.models import EmailMessage, Extraction, ExtractionStatus
from app.services.extraction import (
    EXTRACT_TOOL,
    TRIAGE_TOOL,
    Booking,
    OpenRouterModel,
    TriageResult,
    correct_year,
    extract_selected,
    process_email,
    run_extractions,
    validate_booking,
    validate_bookings,
)


# --------------------------------------------------------------------------
# A fake model
# --------------------------------------------------------------------------


class FakeModel:
    """Canned triage and extraction, and a record of what it was asked."""

    extract_model = "anthropic/claude-sonnet-5"

    def __init__(self, triage: TriageResult, extract=None):
        self._triage = triage
        self._extract = extract
        self.triaged: list[tuple[str, str]] = []
        self.extracted: list[tuple[str, str]] = []

    def triage(self, subject, body):
        self.triaged.append((subject, body))
        return self._triage

    def extract(self, subject, body, received_on=None):
        self.extracted.append((subject, body, received_on))
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


# --------------------------------------------------------------------------
# validate_bookings -- one email can carry more than one booking
# --------------------------------------------------------------------------


def test_validate_bookings_reads_the_bookings_array():
    outbound = {**VALID_BOOKING, "country_code": "MY"}
    inbound = {**VALID_BOOKING, "country_code": "ID"}
    bookings = validate_bookings({"bookings": [outbound, inbound]})
    assert [b.country_code for b in bookings] == ["MY", "ID"]


def test_validate_bookings_drops_only_the_unusable_entries():
    bookings = validate_bookings(
        {"bookings": [VALID_BOOKING, {"kind": "banana"}, {"not": "a booking"}]}
    )
    assert len(bookings) == 1
    assert bookings[0].country_code == "VN"


def test_validate_bookings_tolerates_a_bare_single_booking():
    """Defensive: a provider that ignores strict and returns one flat booking,
    or the older single-booking shape, still validates."""
    bookings = validate_bookings(VALID_BOOKING)
    assert len(bookings) == 1


def test_validate_bookings_of_nothing_is_empty():
    assert validate_bookings(None) == []
    assert validate_bookings({"bookings": []}) == []
    assert validate_bookings({"bookings": "not a list"}) == []


def test_run_extractions_counts_each_journey_of_a_round_trip(session: Session):
    _candidate(session)
    outbound = {**VALID_BOOKING, "kind": "ferry", "country_code": "MY"}
    inbound = {**outbound, "country_code": "ID"}
    model = FakeModel(TriageResult(True, 0.9, "y"), {"bookings": [outbound, inbound]})

    result = run_extractions(session, model)

    assert result == {"processed": 1, "proposed": 2}
    assert len(_extractions(session)) == 2


def test_process_email_returns_the_extractions_created(session: Session):
    email = _candidate(session)
    yes = FakeModel(TriageResult(True, 0.9, "y"), VALID_BOOKING)
    assert len(process_email(session, yes, email)) == 1

    email2 = _candidate(session, uid=11)
    no = FakeModel(TriageResult(False, 0.1, "n"))
    assert process_email(session, no, email2) == []


# --------------------------------------------------------------------------
# Phase 4: extract_selected -- the manual, operator-initiated bypass (D2)
# --------------------------------------------------------------------------


def test_extract_selected_ignores_the_looks_like_travel_gate(session: Session):
    """The whole point of the manual path: a message the auto-filter never
    flagged can still be extracted once a human picks it."""
    email = _candidate(session, looks_like_travel=False)
    model = FakeModel(TriageResult(True, 0.9, "unused"), VALID_BOOKING)

    result = extract_selected(session, model, email, "a live-refetched body")

    assert len(result) == 1
    assert result[0].status == ExtractionStatus.pending
    assert result[0].email_message_id == email.id


def test_extract_selected_never_calls_triage(session: Session):
    """Cost/rate (phase 4's gotcha): the operator already decided; only the
    extract model runs, never the cheap triage pass."""
    email = _candidate(session)
    model = FakeModel(TriageResult(False, 0.0, "would triage out"), VALID_BOOKING)

    result = extract_selected(session, model, email, "body")

    assert len(result) == 1  # triage's False was never consulted
    assert model.triaged == []


def test_extract_selected_uses_the_body_it_is_given_not_the_stored_snippet(
    session: Session,
):
    email = _candidate(session, snippet="stale truncated snippet")
    model = FakeModel(TriageResult(True, 0.9, "y"), VALID_BOOKING)

    extract_selected(session, model, email, "the full live body")

    assert model.extracted[0][1] == "the full live body"


def test_extract_selected_marks_processed_even_when_nothing_is_extracted(
    session: Session,
):
    email = _candidate(session)
    model = FakeModel(TriageResult(True, 0.9, "y"), None)

    result = extract_selected(session, model, email, "body")

    assert result == []
    session.refresh(email)
    assert email.processed_at is not None


def test_extract_selected_records_both_journeys_of_a_round_trip(session: Session):
    """A round-trip ticket is two arrivals into two countries, so it yields two
    proposals -- each matched and accepted into its own trip later."""
    email = _candidate(session, looks_like_travel=False)
    outbound = {**VALID_BOOKING, "kind": "ferry", "country_code": "MY",
                "city": "Johor Bahru", "start_date": "2026-08-07", "end_date": None,
                "hotel_name": None, "carrier": "redBus"}
    inbound = {**outbound, "country_code": "ID", "city": "Batam",
               "start_date": "2026-08-11"}
    model = FakeModel(TriageResult(True, 0.9, "y"), {"bookings": [outbound, inbound]})

    result = extract_selected(session, model, email, "full round-trip body")

    assert len(result) == 2
    countries = {json.loads(e.payload_json)["country_code"] for e in result}
    assert countries == {"MY", "ID"}


# --------------------------------------------------------------------------
# The wrong-year guard: a confirmation is never for a past stay
# --------------------------------------------------------------------------


def _hotel(start: str, end: str | None = None) -> Booking:
    return validate_booking(
        {"kind": "hotel", "country_code": "VN", "city": "Hanoi",
         "start_date": start, "end_date": end}
    )


def test_correct_year_rolls_a_wrong_year_checkin_forward():
    # The reported bug: email received in 2026, model read the year as 2025.
    booking = _hotel("2025-08-10", "2025-08-14")
    fixed = correct_year(booking, date(2026, 7, 15))
    assert fixed.start_date == "2026-08-10"
    # Checkout shifts with it, so the stay keeps its four nights.
    assert fixed.end_date == "2026-08-14"


def test_correct_year_bridges_more_than_one_year():
    booking = _hotel("2024-03-01", "2024-03-05")
    fixed = correct_year(booking, date(2026, 1, 20))
    assert fixed.start_date == "2026-03-01"
    assert fixed.end_date == "2026-03-05"


def test_correct_year_leaves_a_future_date_alone():
    booking = _hotel("2026-08-10", "2026-08-14")
    assert correct_year(booking, date(2026, 7, 15)) == booking


def test_correct_year_leaves_a_date_booked_days_before_check_in_alone():
    # Booked the same week you travel: check-in a few days before the email
    # would be received is inside the slack, not a wrong-year read.
    booking = _hotel("2026-07-10", "2026-07-12")
    assert correct_year(booking, date(2026, 7, 12)) == booking


def test_correct_year_does_not_touch_a_same_year_past_date():
    # A same-year past date (a stray receipt) is not the cross-year bug; leave
    # it for the human rather than inventing a future booking.
    booking = _hotel("2026-01-10", "2026-01-12")
    assert correct_year(booking, date(2026, 9, 1)) == booking


def test_correct_year_needs_a_received_date():
    booking = _hotel("2025-08-10", "2025-08-14")
    assert correct_year(booking, None) == booking


def test_process_email_corrects_the_extracted_year(session: Session):
    email = _candidate(session, received_at=datetime(2026, 7, 15, 9, 0))
    wrong_year = {**VALID_BOOKING, "start_date": "2025-08-30", "end_date": "2025-09-03"}
    model = FakeModel(TriageResult(True, 0.9, "yes"), wrong_year)

    process_email(session, model, email)
    session.commit()

    payload = json.loads(_extractions(session)[0].payload_json)
    assert payload["start_date"] == "2026-08-30"
    assert payload["end_date"] == "2026-09-03"


def test_extract_is_handed_the_received_date(session: Session):
    email = _candidate(session, received_at=datetime(2026, 7, 15, 9, 0))
    model = FakeModel(TriageResult(True, 0.9, "yes"), VALID_BOOKING)

    process_email(session, model, email)

    # The extractor was given the anchor, not just subject and body.
    assert model.extracted[0][2] == date(2026, 7, 15)


# --------------------------------------------------------------------------
# The real Anthropic client, against a mock transport -- exercises the strict
# tool schema without a network call
# --------------------------------------------------------------------------


def _openrouter_tool_response(tool_name: str, tool_input: dict) -> httpx.Response:
    """A Chat Completions response with a single tool call.

    Note the OpenAI shape: the arguments are a JSON *string*, not an object.
    """
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "anthropic/claude-sonnet-5",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_test",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(tool_input),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        },
    )


def _model_with_transport(handler) -> OpenRouterModel:
    from openai import OpenAI

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenRouterModel(
        client, "anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-5"
    )


def test_real_client_sends_strict_tools_and_reads_the_tool_call(session: Session):
    """The strict schema goes out on the wire; the tool call comes back."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.update(payload)
        name = payload["tool_choice"]["function"]["name"]
        if name == TRIAGE_TOOL["name"]:
            return _openrouter_tool_response(
                name, {"is_booking": True, "confidence": 0.9, "reason": "ok"}
            )
        return _openrouter_tool_response(name, {"bookings": [VALID_BOOKING]})

    model = _model_with_transport(handler)

    triage = model.triage("Your booking", "Sofitel, Hanoi")
    assert triage.is_booking is True
    # The function tool that went out was declared strict, in OpenAI shape.
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["function"]["strict"] is True
    assert sent["tools"][0]["function"]["name"] == TRIAGE_TOOL["name"]
    assert sent["model"] == "anthropic/claude-haiku-4.5"

    bookings = validate_bookings(model.extract("Your booking", "Sofitel, Hanoi"))
    assert len(bookings) == 1
    assert bookings[0].country_code == "VN"
    assert sent["tools"][0]["function"]["name"] == EXTRACT_TOOL["name"]
    assert sent["model"] == "anthropic/claude-sonnet-5"


def test_real_client_puts_the_received_date_in_the_prompt(session: Session):
    """The year anchor goes out on the wire as a system message."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.update(payload)
        return _openrouter_tool_response(
            EXTRACT_TOOL["name"], {"bookings": [VALID_BOOKING]}
        )

    model = _model_with_transport(handler)
    model.extract("Your booking", "Sofitel, Hanoi", date(2026, 7, 15))

    system = [m for m in sent["messages"] if m["role"] == "system"]
    assert len(system) == 1
    assert "2026-07-15" in system[0]["content"]


def test_real_client_omits_the_system_message_without_a_date(session: Session):
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return _openrouter_tool_response(
            EXTRACT_TOOL["name"], {"bookings": [VALID_BOOKING]}
        )

    model = _model_with_transport(handler)
    model.extract("Your booking", "Sofitel, Hanoi")

    assert [m for m in sent["messages"] if m["role"] == "system"] == []


def test_real_client_end_to_end_creates_a_pending_extraction(session: Session):
    email = _candidate(session)

    def handler(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["tool_choice"]["function"]["name"]
        if name == TRIAGE_TOOL["name"]:
            return _openrouter_tool_response(
                name, {"is_booking": True, "confidence": 0.95, "reason": "booking"}
            )
        return _openrouter_tool_response(name, {"bookings": [VALID_BOOKING]})

    model = _model_with_transport(handler)

    result = run_extractions(session, model)

    assert result == {"processed": 1, "proposed": 1}
    rows = _extractions(session)
    assert len(rows) == 1
    assert rows[0].status == ExtractionStatus.pending
    assert rows[0].email_message_id == email.id
    # The recorded model is the OpenRouter slug used for extraction.
    assert rows[0].model == "anthropic/claude-sonnet-5"
