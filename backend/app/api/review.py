"""The review queue.

Proposals extracted from email sit here as `pending` until you decide. The GET
lays out what the model read and where it would go; accept and reject are the
two ways out. Accepting is the only thing in this whole feature that writes trip
data, and it lives behind `services.review.accept_extraction`.
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.common import get_or_404
from app.countries import country_name
from app.db import get_session
from app.models import EmailMessage, Extraction, ExtractionStatus, Stay
from app.services.review import (
    NotAcceptable,
    accept_extraction,
    reject_extraction,
    suggest,
)
from app.services.trips import trip_label

router = APIRouter(prefix="/api/review", tags=["review"])


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
    }


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
