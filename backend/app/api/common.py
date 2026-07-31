"""Shared helpers for the CRUD routers."""

from typing import Optional, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import Session, SQLModel

from app.models import utcnow

T = TypeVar("T", bound=SQLModel)


def apply_update(obj: T, payload: BaseModel) -> T:
    """Apply a PATCH payload, ignoring fields the client did not send.

    exclude_unset is what makes PATCH work: an omitted field means "leave it
    alone", while an explicit null means "clear it". Using exclude_none here
    instead would make it impossible to ever clear a field.

    Clearing needs care. Most text columns are non-nullable with a "" default,
    so writing a literal NULL into them raises IntegrityError. "Clear the hotel
    name" is a legitimate edit, so an explicit null on a non-nullable column
    resets it to that column's declared default instead of failing.
    """
    table = getattr(type(obj), "__table__", None)
    fields = getattr(type(obj), "model_fields", {})

    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None and table is not None:
            column = table.columns.get(key)
            if column is not None and not column.nullable:
                field = fields.get(key)
                value = field.get_default(call_default_factory=True) if field else ""
        setattr(obj, key, value)

    if hasattr(obj, "updated_at"):
        obj.updated_at = utcnow()
    return obj


def get_or_404(session: Session, model: type[T], pk: int, label: str) -> T:
    obj: Optional[T] = session.get(model, pk)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label} {pk} not found")
    return obj


def get_child_or_404(
    session: Session, model: type[T], pk: int, trip_id: int, label: str
) -> T:
    """Fetch a segment and verify it belongs to the trip in the URL.

    Returns 404 rather than 403 on a mismatch: a segment under the wrong trip
    does not exist as far as that URL is concerned, and this avoids confirming
    that some other trip owns that id.
    """
    obj = get_or_404(session, model, pk, label)
    if getattr(obj, "trip_id", None) != trip_id:
        raise HTTPException(status_code=404, detail=f"{label} {pk} not found")
    return obj
