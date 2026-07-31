"""Personal notes.

Top-level because a note need not belong to a trip: "renew passport" is a dated
item with no journey attached, and the calendar should still show it.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, or_, select

from app.api.common import apply_update, get_or_404
from app.db import get_session
from app.models import Note, Trip
from app.schemas import NoteCreate, NoteUpdate

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("")
def list_notes(
    session: Session = Depends(get_session),
    trip_id: Optional[int] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Free-text search"),
) -> list[dict]:
    stmt = select(Note)
    if trip_id is not None:
        stmt = stmt.where(Note.trip_id == trip_id)
    if date_from is not None:
        # A multi-day note overlaps the window if it ends on or after date_from.
        stmt = stmt.where(
            or_(Note.end_date >= date_from, Note.on_date >= date_from)
        )
    if date_to is not None:
        stmt = stmt.where(Note.on_date <= date_to)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Note.title.like(like), Note.body.like(like)))

    notes = session.exec(stmt.order_by(Note.on_date)).all()
    return [
        {
            **n.model_dump(),
            "trip_title": (
                session.get(Trip, n.trip_id).title if n.trip_id else None
            ),
        }
        for n in notes
    ]


@router.post("", status_code=201)
def create_note(payload: NoteCreate, session: Session = Depends(get_session)) -> dict:
    if payload.trip_id is not None:
        get_or_404(session, Trip, payload.trip_id, "trip")
    note = Note(**payload.model_dump())
    session.add(note)
    session.commit()
    session.refresh(note)
    return note.model_dump()


@router.patch("/{note_id}")
def update_note(
    note_id: int, payload: NoteUpdate, session: Session = Depends(get_session)
) -> dict:
    note = get_or_404(session, Note, note_id, "note")
    if payload.trip_id is not None:
        get_or_404(session, Trip, payload.trip_id, "trip")
    apply_update(note, payload)
    session.add(note)
    session.commit()
    session.refresh(note)
    return note.model_dump()


@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int, session: Session = Depends(get_session)) -> None:
    note = get_or_404(session, Note, note_id, "note")
    session.delete(note)
    session.commit()
