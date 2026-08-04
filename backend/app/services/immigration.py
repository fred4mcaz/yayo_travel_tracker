"""Matching a locally-flagged immigration email to a trip, and proposing a
confirmation for the review queue. No LLM anywhere in this file.

`services.email_filter.classify_immigration` already decided, entirely on
this box, that a message *looks like* a government confirmation (arrival
card, e-VOA, ESTA approval...). What this module adds is the other half of
"show if an arrival card has been confirmed via email": which trip the
confirmation belongs to, and turning that into a review-queue proposal --
never a direct write. The accept boundary from `services.review` holds here
too: nothing below `accept_confirmation` touches a `Requirement` row, and
that function only runs when a human accepts the proposal.

Deliberately narrow for this phase: the only requirement kind a proposal here
ever targets is `entry_card` -- the one the worked example (README /
immigration_readiness_plan.md) is about, and the one a bare sender+date match
can respectably claim without reading the email body for details a model
would be needed to extract. Visa/eta confirmations, and reading a real
reference or a nationality out of the body, are Phase 5's job once picking
the email *is* the consent to send it to the extractor.
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
    Requirement,
    RequirementKind,
    RequirementStatus,
    Trip,
    utcnow,
)
from app.services.email_filter import effective_rules, immigration_country_for
from app.services.review import MATCH_SLACK_DAYS, NotAcceptable, _overlaps
from app.services.trips import trip_country_code

log = logging.getLogger("yayo.immigration")


def _todo_entry_card(session: Session, trip_id: int) -> Optional[Requirement]:
    """The trip's outstanding arrival-card requirement, if it has one."""
    return session.exec(
        select(Requirement)
        .where(Requirement.trip_id == trip_id)
        .where(Requirement.kind == RequirementKind.entry_card)
        .where(Requirement.status == RequirementStatus.todo)
    ).first()


# --------------------------------------------------------------------------
# Matching -- read-only. Proposes where a confirmation would go; changes nothing.
# --------------------------------------------------------------------------


def find_matching_trip(session: Session, email: EmailMessage) -> Optional[int]:
    """The trip this immigration email's confirmation belongs to, or None.

    Nothing here reads the email body for a date or a country -- that needs a
    model, and decision 4 keeps the automatic path LLM-free entirely. All
    that's available locally is when the email arrived and, if the sender
    domain happens to map to one (data/rules/email-filter.json's
    immigration_sender_domains), which country it represents. A trip
    qualifies when its dated span covers the email's arrival date within
    review.py's own matching slack, it still has an outstanding entry_card
    requirement to confirm, and -- only when the sender's country is known --
    that is also the trip's country. More than one qualifying trip is
    ambiguous; a human decides rather than a guess.
    """
    if email.received_at is None:
        return None
    received_on = email.received_at.date()
    span = (received_on, received_on)
    country = immigration_country_for(email.from_addr, effective_rules(session))

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
        if _todo_entry_card(session, trip.id) is None:
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
    """Flip the matched trip's entry_card requirement to approved.

    Mirrors review.accept_extraction's boundary: nothing above this function
    touches trip data, and this only runs when a human accepts. The row is
    stamped `source=email` -- the traveller's own confirmed record, which
    sync_requirements (Phase 2) is built to never overwrite, even if the
    policy would no longer require an arrival card.
    """
    if extraction.status != ExtractionStatus.pending:
        raise NotAcceptable(f"extraction {extraction.id} is already {extraction.status}")
    if extraction.kind != ExtractionKind.immigration:
        raise NotAcceptable(f"extraction {extraction.id} is not an immigration proposal")
    if extraction.suggested_trip_id is None:
        raise NotAcceptable(f"extraction {extraction.id} has no matched trip")

    requirement = _todo_entry_card(session, extraction.suggested_trip_id)
    if requirement is None:
        # The checklist moved on since the proposal was built -- e.g. the
        # card was already confirmed another way. Nothing left to flip, so
        # refuse rather than silently doing nothing.
        raise NotAcceptable(
            f"trip {extraction.suggested_trip_id} has no outstanding "
            "arrival-card requirement to confirm"
        )

    requirement.status = RequirementStatus.approved
    if reference:
        requirement.reference = reference
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
