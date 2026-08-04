"""Matching an immigration email to a trip, and proposing a confirmation for
the review queue -- both the local, LLM-free path (Phase 4) and the manual,
model-read path (Phase 5).

`services.email_filter.classify_immigration` already decided, entirely on
this box, that a message *looks like* a government confirmation (arrival
card, e-VOA, ESTA approval...). What this module adds is the other half of
"show if an arrival card has been confirmed via email": which trip the
confirmation belongs to, and turning that into a review-queue proposal --
never a direct write. The accept boundary from `services.review` holds here
too: nothing below `accept_confirmation` touches a `Requirement` row, and
that function only runs when a human accepts the proposal.

`propose_confirmation` (Phase 4, fully automatic) only ever targets
`entry_card` -- a bare sender+date match can respectably claim that much
without reading the email body. `extract_selected_immigration` (Phase 5,
manual-only -- picking the email *is* the consent) reads a real requirement
kind, a reference, and a nationality out of the body via a model, and the
nationality feeds the loud discrepancy flag (decision 3): stored on the
`Requirement` row as the raw fact, compared against the trip's *currently*
selected passport at read time by `services.trips.trip_readiness`.
"""

import json
import logging
from typing import Optional

from sqlmodel import Session, select

from app.models import (
    Actor,
    EmailMessage,
    Extraction,
    ExtractionKind,
    ExtractionStatus,
    Nationality,
    Requirement,
    RequirementKind,
    RequirementStatus,
    Trip,
    utcnow,
)
from app.services.email_filter import effective_rules, immigration_country_for
from app.services.extraction import ExtractionModel, validate_immigration_document
from app.services.review import MATCH_SLACK_DAYS, NotAcceptable, _overlaps
from app.services.trips import trip_country_code

log = logging.getLogger("yayo.immigration")


def _todo_requirement(
    session: Session, trip_id: int, kind: RequirementKind
) -> Optional[Requirement]:
    """The trip's outstanding requirement of this kind, if it has one."""
    return session.exec(
        select(Requirement)
        .where(Requirement.trip_id == trip_id)
        .where(Requirement.kind == kind)
        .where(Requirement.status == RequirementStatus.todo)
    ).first()


# --------------------------------------------------------------------------
# Matching -- read-only. Proposes where a confirmation would go; changes nothing.
# --------------------------------------------------------------------------


def find_matching_trip(
    session: Session,
    email: EmailMessage,
    *,
    kind: RequirementKind = RequirementKind.entry_card,
    country_hint: Optional[str] = None,
) -> Optional[int]:
    """The trip this immigration email's confirmation belongs to, or None.

    The local (Phase 4) path never reads the email body for a date or a
    country -- that needs a model, and decision 4 keeps the automatic path
    LLM-free entirely. All that's available there is when the email arrived
    and, if the sender domain happens to map to one
    (data/rules/email-filter.json's immigration_sender_domains), which
    country it represents. The manual (Phase 5) path has read the body, so
    it can pass a stronger `country_hint` from the model's own reading --
    when given, that wins over the sender-domain guess.

    A trip qualifies when its dated span covers the email's arrival date
    within review.py's own matching slack, it still has an outstanding
    requirement of `kind` to confirm, and -- only when a country is known --
    that is also the trip's country. More than one qualifying trip is
    ambiguous; a human decides rather than a guess.
    """
    if email.received_at is None:
        return None
    received_on = email.received_at.date()
    span = (received_on, received_on)
    country = country_hint or immigration_country_for(
        email.from_addr, effective_rules(session)
    )

    candidates: list[int] = []
    trips = session.exec(
        select(Trip)
        .where(Trip.start_date.is_not(None))
        .where(Trip.end_date.is_not(None))
    ).all()
    for trip in trips:
        if not _overlaps(span, (trip.start_date, trip.end_date), MATCH_SLACK_DAYS):
            continue
        if country is not None:
            code = trip_country_code(session, trip.id)
            if code is not None and code != country:
                continue
        if _todo_requirement(session, trip.id, kind) is None:
            continue
        candidates.append(trip.id)

    if len(candidates) != 1:
        return None
    return candidates[0]


def propose_confirmation(session: Session, email: EmailMessage) -> Optional[Extraction]:
    """Build (or return the existing) immigration confirmation proposal for
    this email, if it matches exactly one trip. Idempotent per email -- a
    message already proposed is returned as-is, never duplicated, mirroring
    how extract_email's manual path avoids re-extracting.

    Refuses an email the local classifier never flagged, even if it would
    otherwise match a trip -- defence in depth, so a future call site cannot
    accidentally propose from mail classify_immigration rejected.
    """
    if not email.looks_like_immigration:
        return None

    existing = session.exec(
        select(Extraction)
        .where(Extraction.email_message_id == email.id)
        .where(Extraction.kind == ExtractionKind.immigration)
    ).first()
    if existing is not None:
        return existing

    trip_id = find_matching_trip(session, email)
    if trip_id is None:
        return None

    extraction = Extraction(
        email_message_id=email.id,
        kind=ExtractionKind.immigration,
        model="",
        payload_json=json.dumps({"requirement_kind": RequirementKind.entry_card.value}),
        confidence=None,
        suggested_trip_id=trip_id,
    )
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    log.info(
        "proposed immigration confirmation for email %s -> trip %d", email.id, trip_id
    )
    return extraction


def run_immigration_matching(session: Session, *, limit: int = 200) -> dict:
    """One pass: every flagged email without an immigration proposal yet gets
    a matching attempt. No model call anywhere, so unlike run_extractions this
    needs no OpenRouter key -- safe to run on every poll regardless.
    """
    already_proposed = select(Extraction.email_message_id).where(
        Extraction.kind == ExtractionKind.immigration
    )
    rows = session.exec(
        select(EmailMessage)
        .where(EmailMessage.looks_like_immigration == True)  # noqa: E712
        .where(EmailMessage.id.not_in(already_proposed))
        .order_by(EmailMessage.received_at)
        .limit(limit)
    ).all()

    proposed = 0
    for email in rows:
        if propose_confirmation(session, email) is not None:
            proposed += 1
    session.commit()
    if rows:
        log.info("immigration matching: %d checked, %d proposed", len(rows), proposed)
    return {"checked": len(rows), "proposed": proposed}


# --------------------------------------------------------------------------
# The accept boundary -- the only writer of Requirement data in this module
# --------------------------------------------------------------------------


def accept_confirmation(
    session: Session, extraction: Extraction, reference: str = ""
) -> Requirement:
    """Flip the matched trip's requirement to approved.

    Mirrors review.accept_extraction's boundary: nothing above this function
    touches trip data, and this only runs when a human accepts. The row is
    stamped `source=email` -- the traveller's own confirmed record, which
    sync_requirements (Phase 2) is built to never overwrite, even if the
    policy would no longer require it.

    Reads `requirement_kind` from the proposal's own payload (`entry_card`
    for a Phase 4 local match, whatever the model read for a Phase 5 one), so
    the accept endpoint never needs to know which kind it's confirming. Same
    for `reference`: a Phase 5 reading pre-fills it, but a reviewer-typed
    `reference` here always wins. A Phase 5 reading that named a `nationality`
    is stamped onto `discrepancy_nationality` unconditionally -- the raw fact,
    not yet compared to anything; whether it renders as a loud mismatch is
    decided live, at read time, by trip_readiness.
    """
    if extraction.status != ExtractionStatus.pending:
        raise NotAcceptable(f"extraction {extraction.id} is already {extraction.status}")
    if extraction.kind != ExtractionKind.immigration:
        raise NotAcceptable(f"extraction {extraction.id} is not an immigration proposal")
    if extraction.suggested_trip_id is None:
        raise NotAcceptable(f"extraction {extraction.id} has no matched trip")

    payload = json.loads(extraction.payload_json)
    kind = RequirementKind(payload.get("requirement_kind") or RequirementKind.entry_card.value)

    requirement = _todo_requirement(session, extraction.suggested_trip_id, kind)
    if requirement is None:
        # The checklist moved on since the proposal was built -- e.g. the
        # item was already confirmed another way. Nothing left to flip, so
        # refuse rather than silently doing nothing.
        raise NotAcceptable(
            f"trip {extraction.suggested_trip_id} has no outstanding "
            f"{kind.value} requirement to confirm"
        )

    requirement.status = RequirementStatus.approved
    if reference:
        requirement.reference = reference
    elif payload.get("reference"):
        requirement.reference = payload["reference"]
    if payload.get("nationality"):
        requirement.discrepancy_nationality = Nationality(payload["nationality"])
    requirement.source = Actor.email
    session.add(requirement)

    extraction.status = ExtractionStatus.accepted
    extraction.reviewed_at = utcnow()
    extraction.applied_ids_json = json.dumps({"requirement_id": requirement.id})
    session.add(extraction)
    session.commit()
    session.refresh(requirement)

    log.info(
        "accepted immigration confirmation %s -> requirement %d approved",
        extraction.id,
        requirement.id,
    )
    return requirement


# --------------------------------------------------------------------------
# Phase 5 -- the manual, model-read path (D2: picking the email is consent)
# --------------------------------------------------------------------------


def extract_selected_immigration(
    session: Session, model: ExtractionModel, email: EmailMessage, body: str
) -> Optional[Extraction]:
    """Extract one email on operator-initiated demand, reading a requirement
    kind, a reference, and a nationality out of the body -- the richer read
    a bare sender+date match never could.

    D2: picking this email (from the recent-emails list, flagged or not) *is*
    the informed, per-message consent that would otherwise come from the
    local classifier passing -- same rule as extract_selected's booking
    counterpart. Matches the same way propose_confirmation does (date +
    country, review.py's slack), but the kind and the country can come from
    the reading itself rather than only the sender's domain.

    Marks the email processed either way, so a manual attempt that finds
    nothing usable is not retried by a later automatic pass.
    """
    raw = model.extract_immigration_document(email.subject, body)
    document = validate_immigration_document(raw)
    if document is None:
        email.processed_at = utcnow()
        session.add(email)
        session.commit()
        return None

    kind = document.requirement_kind or RequirementKind.entry_card
    trip_id = find_matching_trip(
        session, email, kind=kind, country_hint=document.country_code
    )

    extraction = Extraction(
        email_message_id=email.id,
        kind=ExtractionKind.immigration,
        model=model.extract_model,
        payload_json=json.dumps(
            {
                "requirement_kind": kind.value,
                "nationality": document.nationality.value if document.nationality else None,
                "reference": document.reference or "",
            }
        ),
        confidence=document.confidence,
        suggested_trip_id=trip_id,
    )
    session.add(extraction)
    email.processed_at = utcnow()
    session.add(email)
    session.commit()
    session.refresh(extraction)
    log.info(
        "manually extracted immigration document for email %s -> trip %s",
        email.id,
        trip_id,
    )
    return extraction
