"""Phase 4 + 5: matching an immigration email to a trip -- both the local,
LLM-free path (Phase 4) and the manual, model-read path (Phase 5) -- and the
review-queue proposal either becomes.

The invariant carried over from Phase 4's booking counterpart (test_review.py):

    Nothing writes to a Requirement until accept_confirmation runs.

So matching and proposing are asserted read-only (row-count / status
untouched), and only accept is asserted to flip anything. Phase 5 adds the
loud discrepancy flag (decision 3): a live comparison at trip_readiness read
time, not a stored verdict -- see the tests near the bottom of this file.
"""

import json
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.models import (
    Actor,
    CountryEntry,
    EmailMessage,
    Extraction,
    ExtractionKind,
    ExtractionStatus,
    Nationality,
    Passport,
    Requirement,
    RequirementKind,
    RequirementStatus,
    Stay,
    Trip,
)
from app.services.immigration import (
    accept_confirmation,
    extract_selected_immigration,
    find_matching_trip,
    propose_confirmation,
    run_immigration_matching,
)
from app.services.review import NotAcceptable, reject_extraction
from app.services.trips import refresh_trip_dates, sync_country_entries, trip_readiness

_next_uid = iter(range(200, 200_000))


def _make_trip(session: Session, country: str, city: str, check_in: str, check_out: str) -> Trip:
    trip = Trip(notes="")
    session.add(trip)
    session.commit()
    session.refresh(trip)
    session.add(
        Stay(
            trip_id=trip.id,
            country_code=country,
            city=city,
            check_in=date.fromisoformat(check_in),
            check_out=date.fromisoformat(check_out),
        )
    )
    session.commit()
    refresh_trip_dates(session, trip)
    session.commit()
    sync_country_entries(session, trip)
    session.refresh(trip)
    return trip


def _entry_card(session: Session, trip: Trip, status=RequirementStatus.todo) -> Requirement:
    req = Requirement(
        trip_id=trip.id,
        kind=RequirementKind.entry_card,
        label="Arrival card",
        status=status,
        country_code="",
        source=Actor.system,
    )
    session.add(req)
    session.commit()
    session.refresh(req)
    return req


def _email(
    session: Session,
    *,
    from_addr: str = "no-reply@imigrasi.go.id",
    subject: str = "Your Indonesia e-CD is confirmed",
    snippet: str = "Your electronic customs declaration has been approved.",
    received_at: Optional[datetime] = datetime(2026, 9, 11, 9, 0),
    looks_like_immigration: bool = True,
    looks_like_travel: bool = False,
) -> EmailMessage:
    uid = next(_next_uid)
    email = EmailMessage(
        imap_uid=uid,
        message_id=f"<{uid}@mail.example>",
        from_addr=from_addr,
        subject=subject,
        snippet=snippet,
        received_at=received_at,
        looks_like_immigration=looks_like_immigration,
        looks_like_travel=looks_like_travel,
    )
    session.add(email)
    session.commit()
    session.refresh(email)
    return email


def _counts(session: Session) -> tuple[int, str]:
    """(requirement count, the one requirement's status) -- enough to prove
    matching and rejecting touch nothing."""
    reqs = session.exec(select(Requirement)).all()
    return (len(reqs), reqs[0].status.value if reqs else "")


# --------------------------------------------------------------------------
# Matching -- read only
# --------------------------------------------------------------------------


def test_matches_a_trip_by_date_and_country_with_an_outstanding_arrival_card(
    session: Session,
):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))

    assert find_matching_trip(session, email) == trip.id


def test_no_match_when_the_trip_has_no_outstanding_arrival_card(session: Session):
    """Already approved, or never required at all -- either way there is
    nothing to confirm, so this must not match."""
    _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))

    assert find_matching_trip(session, email) is None


def test_no_match_when_the_email_falls_outside_the_trip_span(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 11, 1, 9, 0))

    assert find_matching_trip(session, email) is None


def test_no_match_when_the_sender_country_disagrees_with_the_trip(session: Session):
    """imigrasi.go.id is mapped to ID; a Malaysia trip in the same window is
    not this confirmation's home, even though the dates overlap."""
    trip = _make_trip(session, "MY", "Kuala Lumpur", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))

    assert find_matching_trip(session, email) is None


def test_ambiguous_when_two_trips_qualify(session: Session):
    """Two concurrent Indonesia trips, both with an outstanding card, both in
    the window -- a human decides rather than a guess."""
    trip_a = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    trip_b = _make_trip(session, "ID", "Jakarta", "2026-09-11", "2026-09-13")
    _entry_card(session, trip_a)
    _entry_card(session, trip_b)
    email = _email(session, received_at=datetime(2026, 9, 12, 9, 0))

    assert find_matching_trip(session, email) is None


def test_no_match_without_a_received_date(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=None)

    assert find_matching_trip(session, email) is None


def test_matching_is_read_only(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    before = _counts(session)

    find_matching_trip(session, email)

    assert _counts(session) == before


# --------------------------------------------------------------------------
# Proposing -- still read-only on Requirement, but writes an Extraction
# --------------------------------------------------------------------------


def test_a_stubbed_indonesian_arrival_card_email_proposes_a_confirmation(
    session: Session,
):
    """The Phase 4 headline scenario: flags immigration (never travel), and
    proposes a confirmation on the matching Indonesia trip."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    assert email.looks_like_immigration is True
    assert email.looks_like_travel is False

    extraction = propose_confirmation(session, email)

    assert extraction is not None
    assert extraction.kind == ExtractionKind.immigration
    assert extraction.status == ExtractionStatus.pending
    assert extraction.suggested_trip_id == trip.id
    # Read-only on the requirement itself -- only accept may flip it.
    assert _counts(session) == (1, "todo")


def test_propose_confirmation_is_idempotent_per_email(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))

    first = propose_confirmation(session, email)
    second = propose_confirmation(session, email)

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert len(
        session.exec(
            select(Extraction).where(Extraction.email_message_id == email.id)
        ).all()
    ) == 1


def test_a_non_immigration_email_is_untouched(session: Session):
    """No proposal, and the flag on the email is exactly what it started as --
    services.immigration never mutates the EmailMessage it reads."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(
        session,
        from_addr="mum@gmail.com",
        subject="call me when you land",
        snippet="have a safe trip",
        looks_like_immigration=False,
        looks_like_travel=False,
    )

    assert propose_confirmation(session, email) is None
    assert _counts(session) == (1, "todo")
    assert session.exec(select(Extraction)).all() == []


def test_run_immigration_matching_proposes_for_every_flagged_unmatched_email(
    session: Session,
):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    flagged = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    _email(  # not flagged -- must be skipped
        session,
        from_addr="mum@gmail.com",
        looks_like_immigration=False,
        received_at=datetime(2026, 9, 11, 9, 0),
    )

    result = run_immigration_matching(session)

    assert result == {"checked": 1, "proposed": 1}
    assert (
        session.exec(
            select(Extraction).where(Extraction.email_message_id == flagged.id)
        ).first()
        is not None
    )

    # A second pass finds nothing new to check.
    result2 = run_immigration_matching(session)
    assert result2 == {"checked": 0, "proposed": 0}


# --------------------------------------------------------------------------
# The accept boundary -- the only writer of Requirement data
# --------------------------------------------------------------------------


def test_accept_flips_entry_card_to_approved_with_the_reference(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    req = _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    extraction = propose_confirmation(session, email)

    result = accept_confirmation(session, extraction, reference="ECD-99887766")

    assert result.id == req.id
    assert result.status == RequirementStatus.approved
    assert result.reference == "ECD-99887766"
    assert result.source == Actor.email
    session.refresh(extraction)
    assert extraction.status == ExtractionStatus.accepted


def test_accept_without_a_reference_leaves_the_existing_one_alone(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    req = _entry_card(session, trip)
    req.reference = "already-here"
    session.add(req)
    session.commit()
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    extraction = propose_confirmation(session, email)

    result = accept_confirmation(session, extraction, reference="")

    assert result.reference == "already-here"


def test_reject_writes_nothing(session: Session):
    """The row-count assertion, mirroring test_review.py's booking equivalent:
    rejecting is only a status flip on the extraction, never a Requirement
    write."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    extraction = propose_confirmation(session, email)
    before = _counts(session)

    reject_extraction(session, extraction)

    assert _counts(session) == before
    session.refresh(extraction)
    assert extraction.status == ExtractionStatus.rejected


def test_accept_twice_refuses_the_second_time(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    extraction = propose_confirmation(session, email)
    accept_confirmation(session, extraction, reference="ECD-1")

    try:
        accept_confirmation(session, extraction, reference="ECD-2")
        assert False, "expected NotAcceptable"
    except NotAcceptable:
        pass


def test_accept_refuses_when_the_checklist_moved_on(session: Session):
    """The card was confirmed some other way between proposal and accept --
    nothing left to flip, so this refuses rather than silently no-op-ing."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    req = _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    extraction = propose_confirmation(session, email)

    req.status = RequirementStatus.approved
    session.add(req)
    session.commit()

    try:
        accept_confirmation(session, extraction, reference="ECD-1")
        assert False, "expected NotAcceptable"
    except NotAcceptable:
        pass


# --------------------------------------------------------------------------
# Phase 5 -- extract_selected_immigration, the manual model-read path
# --------------------------------------------------------------------------


class FakeExtractionModel:
    extract_model = "anthropic/claude-sonnet-5"

    def __init__(self, result):
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def extract_immigration_document(self, subject, body):
        self.calls.append((subject, body))
        return self._result


VALID_DOC = {
    "requirement_kind": "entry_card",
    "country_code": "ID",
    "nationality": "MX",
    "reference": "ECD-123456",
    "confidence": 0.9,
}


def test_extract_selected_immigration_matches_and_records_the_reading(session: Session):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(
        session,
        from_addr="mum@gmail.com",  # unflagged sender -- picking it is the consent
        looks_like_immigration=False,
        received_at=datetime(2026, 9, 11, 9, 0),
    )
    model = FakeExtractionModel(VALID_DOC)

    extraction = extract_selected_immigration(session, model, email, email.snippet)

    assert extraction is not None
    assert extraction.kind == ExtractionKind.immigration
    assert extraction.suggested_trip_id == trip.id
    assert extraction.model == "anthropic/claude-sonnet-5"
    assert extraction.confidence == 0.9
    payload = json.loads(extraction.payload_json)
    assert payload == {
        "requirement_kind": "entry_card",
        "nationality": "MX",
        "reference": "ECD-123456",
    }
    session.refresh(email)
    assert email.processed_at is not None
    # Read-only on the requirement -- only accept may flip it.
    assert _counts(session) == (1, "todo")


def test_extract_selected_immigration_uses_the_country_hint_over_the_sender_domain(
    session: Session,
):
    """The model's own country read beats the sender-domain guess -- here the
    sender is generic but the document names ID."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    other = _make_trip(session, "MY", "Johor Bahru", "2026-09-10", "2026-09-14")
    _entry_card(session, other)

    email = _email(
        session,
        from_addr="forwarded@gmail.com",
        looks_like_immigration=False,
        received_at=datetime(2026, 9, 11, 9, 0),
    )
    model = FakeExtractionModel(VALID_DOC)  # country_code: ID

    extraction = extract_selected_immigration(session, model, email, email.snippet)

    assert extraction is not None
    assert extraction.suggested_trip_id == trip.id


def test_extract_selected_immigration_can_target_a_kind_other_than_entry_card(
    session: Session,
):
    """Phase 4's local matcher only ever confirms entry_card; the manual,
    model-read path can confirm any kind the document actually is."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    visa = Requirement(
        trip_id=trip.id,
        kind=RequirementKind.visa,
        status=RequirementStatus.todo,
        source=Actor.system,
    )
    session.add(visa)
    session.commit()

    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    model = FakeExtractionModel({**VALID_DOC, "requirement_kind": "visa"})

    extraction = extract_selected_immigration(session, model, email, email.snippet)
    assert extraction.suggested_trip_id == trip.id

    result = accept_confirmation(session, extraction, reference="")
    assert result.kind == RequirementKind.visa
    assert result.status == RequirementStatus.approved
    assert result.reference == "ECD-123456"  # pre-filled from the reading


def test_extract_selected_immigration_marks_processed_when_the_reading_is_unusable(
    session: Session,
):
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    model = FakeExtractionModel(None)

    result = extract_selected_immigration(session, model, email, email.snippet)

    assert result is None
    session.refresh(email)
    assert email.processed_at is not None
    assert session.exec(select(Extraction)).all() == []


# --------------------------------------------------------------------------
# Phase 5 -- the loud discrepancy flag (decision 3)
# --------------------------------------------------------------------------


def test_no_discrepancy_when_the_document_nationality_matches_the_selected_passport(
    session: Session,
):
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    model = FakeExtractionModel({**VALID_DOC, "nationality": "US"})  # matches default

    extraction = extract_selected_immigration(session, model, email, email.snippet)
    accept_confirmation(session, extraction, reference="")

    assert trip_readiness(session, trip)["discrepancy"] is None


def test_discrepancy_shows_after_accepting_a_mismatched_nationality(session: Session):
    """Decision 3, at the service level: an MX-nationality document accepted
    on a trip with no passport chosen (reads as the US default) must flag,
    and accept must still have gone through -- the card is confirmed either
    way, the flag is additional, not a block."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    model = FakeExtractionModel(VALID_DOC)  # nationality: MX

    extraction = extract_selected_immigration(session, model, email, email.snippet)
    result = accept_confirmation(session, extraction, reference="")

    assert result.status == RequirementStatus.approved  # still confirmed
    readiness = trip_readiness(session, trip)
    assert readiness["discrepancy"] == {
        "kind": "entry_card",
        "document_nationality": "MX",
        "selected_passport": "US",
    }


def test_discrepancy_clears_once_the_passport_is_flipped_to_match(session: Session):
    """The flag is a live comparison, not a stored verdict -- selecting the MX
    passport the email actually matches clears it without touching the row
    accept wrote."""
    trip = _make_trip(session, "ID", "Batam", "2026-09-10", "2026-09-14")
    _entry_card(session, trip)
    email = _email(session, received_at=datetime(2026, 9, 11, 9, 0))
    model = FakeExtractionModel(VALID_DOC)  # nationality: MX
    extraction = extract_selected_immigration(session, model, email, email.snippet)
    accept_confirmation(session, extraction, reference="")
    assert trip_readiness(session, trip)["discrepancy"] is not None

    mx_passport = Passport(nationality=Nationality.MX)
    session.add(mx_passport)
    session.commit()
    session.refresh(mx_passport)
    entry = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip.id)
    ).first()
    entry.passport_id = mx_passport.id
    session.add(entry)
    session.commit()

    assert trip_readiness(session, trip)["discrepancy"] is None
