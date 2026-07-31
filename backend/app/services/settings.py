"""Small mutable key/value state, in the database rather than a file.

The IMAP watermark has to move in the same transaction as the rows it accounts
for. A file in var/ cannot do that: a crash between "rows committed" and "file
written" leaves the two disagreeing, and the next run re-ingests.
"""

from typing import Optional

from sqlmodel import Session, select

from app.models import Setting, utcnow


def get_setting(session: Session, key: str) -> Optional[str]:
    """The stored value, or None if the key has never been written."""
    row = session.exec(select(Setting).where(Setting.key == key)).first()
    return row.value if row else None


def set_setting(session: Session, key: str, value: str) -> Setting:
    """Write a value. Does not commit -- the caller decides the transaction."""
    row = session.exec(select(Setting).where(Setting.key == key)).first()
    if row is None:
        row = Setting(key=key, value=value)
    else:
        row.value = value
        row.updated_at = utcnow()
    session.add(row)
    return row
