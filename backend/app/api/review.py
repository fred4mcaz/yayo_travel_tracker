"""The review queue.

Proposals extracted from email sit here as `pending` until you decide. The GET
lays out what the model read and where it would go; accept and reject are the
two ways out. Accepting is the only thing in this whole feature that writes trip
data, and it lives behind `services.review.accept_extraction`.
"""

import json
import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.common import get_or_404
from app.config import Settings, get_settings
from app.countries import country_name
from app.db import engine, get_session
from app.models import EmailMessage, Extraction, ExtractionStatus, Stay, utcnow
from app.services.email_ingest import ImapMailbox
from app.services.extraction import OpenRouterModel, extract_selected
from app.services.review import (
    NotAcceptable,
    accept_extraction,
    reject_extraction,
    suggest,
)
from app.services.scheduler import missing_credentials, run_poll_cycle
from app.services.trips import trip_label

log = logging.getLogger("yayo.review")

router = APIRouter(prefix="/api/review", tags=["review"])

# How far back "Find recent emails" looks by default -- long enough to catch
# a booking missed a day or two ago, short enough that the list stays a quick
# scan rather than a mailbox dump.
DEFAULT_RECENT_DAYS = 3


class AcceptPayload(BaseModel):
    # All optional: the reviewer overrides only what the model got wrong.
    kind: Optional[str] = None
    country_code: Optional[str] = None
    city: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    hotel_name: Optional[str] = None
    carrier: Optional[str] = None
    confirmation_code: Optional[str] = None


def _trip_label(session: Session, trip_id: int) -> str:
    stays = session.exec(select(Stay).where(Stay.trip_id == trip_id)).all()
    return trip_label(stays)


def _serialise(session: Session, extraction: Extraction) -> dict:
    """A proposal, laid out for review: what was read, and where it would go."""
    email = session.get(EmailMessage, extraction.email_message_id)
    booking = json.loads(extraction.payload_json)
    code = booking.get("country_code")

    suggestion = None
    if extraction.suggested_trip_id is not None:
        suggestion = {
            "trip_id": extraction.suggested_trip_id,
            "label": _trip_label(session, extraction.suggested_trip_id),
        }

    return {
        "id": extraction.id,
        "status": extraction.status,
        "model": extraction.model,
        "confidence": extraction.confidence,
        "created_at": extraction.created_at.isoformat(),
        "email": {
            "id": email.id if email else None,
            "from_addr": email.from_addr if email else "",
            "subject": email.subject if email else "",
            "snippet": email.snippet if email else "",
            "received_at": (
                email.received_at.isoformat() if email and email.received_at else None
            ),
        },
        "booking": {
            **booking,
            "country_name": country_name(code) if code else None,
        },
        "suggestion": suggestion,
    }


@router.get("")
def list_review(
    session: Session = Depends(get_session),
    include_reviewed: bool = False,
) -> list[dict]:
    """Pending proposals, newest first. `include_reviewed` shows the history."""
    stmt = select(Extraction)
    if not include_reviewed:
        stmt = stmt.where(Extraction.status == ExtractionStatus.pending)
    rows = session.exec(stmt.order_by(Extraction.created_at.desc())).all()

    # Freshen the match suggestion at read time: a trip added since extraction
    # may now be the right home, and matching is cheap and read-only.
    out = []
    for row in rows:
        if row.status == ExtractionStatus.pending:
            suggest(session, row)
    session.commit()
    for row in rows:
        out.append(_serialise(session, row))
    return out


@router.get("/count")
def pending_count(session: Session = Depends(get_session)) -> dict:
    """For the tab badge -- how many proposals are waiting."""
    rows = session.exec(
        select(Extraction).where(Extraction.status == ExtractionStatus.pending)
    ).all()
    return {"pending": len(rows)}


def _serialise_email(session: Session, email: EmailMessage) -> dict:
    has_pending = (
        session.exec(
            select(Extraction)
            .where(Extraction.email_message_id == email.id)
            .where(Extraction.status == ExtractionStatus.pending)
        ).first()
        is not None
    )
    return {
        "id": email.id,
        "from_addr": email.from_addr,
        "subject": email.subject,
        "snippet": email.snippet,
        "received_at": email.received_at.isoformat() if email.received_at else None,
        "looks_like_travel": email.looks_like_travel,
        "has_pending": has_pending,
    }


@router.get("/recent-emails")
def recent_emails(
    session: Session = Depends(get_session),
    days: int = DEFAULT_RECENT_DAYS,
) -> list[dict]:
    """The manual safety-net list (D2): every stored email from the last
    `days` days -- flagged or not -- so one can be picked for extraction even
    though the automatic filter never touched it.
    """
    since = utcnow() - timedelta(days=days)
    rows = session.exec(
        select(EmailMessage)
        .where(EmailMessage.received_at >= since)
        .order_by(EmailMessage.received_at.desc())
    ).all()
    return [_serialise_email(session, row) for row in rows]


@router.post("/emails/{email_id}/extract")
def extract_email(
    email_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Manually extract one email, bypassing the sender/keyword gate (D2).

    Picking an email here *is* the operator's per-message consent to send
    that one message to the extractor -- a deliberate, logged relaxation of
    the privacy control, never automatic. Returns a list because one email can
    yield several proposals -- a round-trip ticket is two journeys. If pending
    proposals already exist for this email, those are returned rather than
    extracting again.
    """
    email = get_or_404(session, EmailMessage, email_id, "email")

    existing = session.exec(
        select(Extraction)
        .where(Extraction.email_message_id == email.id)
        .where(Extraction.status == ExtractionStatus.pending)
        .order_by(Extraction.created_at)
    ).all()
    if existing:
        for row in existing:
            suggest(session, row)
        session.commit()
        return [_serialise(session, row) for row in existing]

    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=409,
            detail=(
                "Email extraction is not configured. Set "
                "YAYO_OPENROUTER_API_KEY in deploy/.env."
            ),
        )

    # Best-effort: a live re-fetch gets the full body (phase 1's HTML
    # fallback included), correcting for the truncated 400-char snippet.
    # Any failure here -- IMAP unconfigured, UIDVALIDITY changed, the message
    # moved or was deleted -- falls back to what's already stored rather than
    # blocking the extract the operator explicitly asked for.
    body = email.snippet
    try:
        with ImapMailbox.from_settings() as mailbox:
            fetched = mailbox.fetch_by_message_id(email.message_id)
        if fetched is not None:
            body = fetched.body
    except Exception:  # noqa: BLE001 -- degrade to the stored snippet, don't fail
        log.warning(
            "manual extract: live re-fetch failed for email %s; using the "
            "stored snippet",
            email.id,
            exc_info=True,
        )

    model = OpenRouterModel.from_settings()
    extractions = extract_selected(session, model, email, body)
    if not extractions:
        raise HTTPException(
            status_code=422,
            detail="the model found nothing to extract from this email",
        )

    for extraction in extractions:
        suggest(session, extraction)
    session.commit()
    return [_serialise(session, extraction) for extraction in extractions]


@router.post("/{extraction_id}/accept")
def accept(
    extraction_id: int,
    payload: AcceptPayload,
    session: Session = Depends(get_session),
) -> dict:
    extraction = get_or_404(session, Extraction, extraction_id, "extraction")
    overrides = payload.model_dump(exclude_none=True)
    try:
        result = accept_extraction(session, extraction, overrides or None)
    except NotAcceptable as exc:
        # The proposal cannot be turned into a valid record as it stands --
        # a person needs to fix a field. 422, not 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "accepted": True,
        "trip_id": result.trip_id,
        "created_new_trip": result.created_new_trip,
        "stay_id": result.stay_id,
        "leg_id": result.leg_id,
        # Set only when this accept taught the filter a new sender domain
        # (D3) -- the review page uses it for a one-time confirmation note.
        "learned_domain": result.learned_domain,
    }


@router.post("/poll")
def poll_now(settings: Settings = Depends(get_settings)) -> dict:
    """Run one ingest+extraction cycle now, instead of waiting for the timer.

    Gated exactly like the scheduler: a disabled or half-configured box is told
    why nothing ran, rather than appearing to work and quietly doing nothing.
    """
    if not settings.email_ingest_enabled:
        raise HTTPException(
            status_code=409,
            detail=(
                "Email ingest is off. Set YAYO_EMAIL_INGEST_ENABLED=true in "
                "deploy/.env to turn it on."
            ),
        )
    missing = missing_credentials(settings)
    if missing:
        raise HTTPException(
            status_code=409,
            detail="Email ingest is on but not configured: " + ", ".join(missing),
        )
    result = run_poll_cycle(engine)
    return {"polled": True, **result}


@router.post("/{extraction_id}/reject")
def reject(
    extraction_id: int, session: Session = Depends(get_session)
) -> dict:
    extraction = get_or_404(session, Extraction, extraction_id, "extraction")
    try:
        reject_extraction(session, extraction)
    except NotAcceptable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rejected": True}
