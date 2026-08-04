"""Recover one already-stored message the automatic filter never caught.

    python -m app.tasks.reflag_message '<message-id@example.com>'

One-off, not part of the regular poll cycle. Classification is one-shot at
store time (see `email_ingest._store`), so a message that was stored before a
sender was allow-listed -- or before the HTML-body fallback existed -- is
stuck with whatever `looks_like_travel` it got that day, forever, unless
something explicitly revisits it. This is that something.

Located by Message-ID, not the stored `imap_uid`: the row may predate a
UIDVALIDITY change, which would make the old UID point at an unrelated
message or nothing at all. A header search on the mailbox sidesteps that
entirely -- it never depends on UID validity.
"""

import logging
import sys
from typing import Optional, Protocol

from sqlmodel import Session, select

from app.db import engine
from app.models import EmailMessage
from app.services.email_ingest import IncomingEmail, ImapMailbox, _snippet

log = logging.getLogger("yayo.email")


class MessageLookup(Protocol):
    def fetch_by_message_id(self, message_id: str) -> Optional[IncomingEmail]:
        ...


def reflag(session: Session, mailbox: MessageLookup, message_id: str) -> EmailMessage:
    """Re-fetch the full body and flip the stored row back into the queue.

    Sets `looks_like_travel=True` unconditionally -- reaching for this command
    at all is already the human deciding the message belongs in review. It
    does not run the message through the extractor; the next poll or extract
    cycle does that.
    """
    row = session.exec(
        select(EmailMessage).where(EmailMessage.message_id == message_id)
    ).first()
    if row is None:
        raise LookupError(f"no stored email with message_id {message_id!r}")

    email = mailbox.fetch_by_message_id(message_id)
    if email is None:
        raise LookupError(
            f"message {message_id!r} was not found on the mailbox "
            "(deleted, moved to another folder, or never existed there)"
        )

    row.imap_uid = email.uid
    row.snippet = _snippet(email.body)
    row.looks_like_travel = True
    row.processed_at = None
    session.add(row)
    session.commit()
    session.refresh(row)
    log.info("reflagged email %s (%s) for review", row.id, row.from_addr)
    return row


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m app.tasks.reflag_message <message-id>", file=sys.stderr)
        return 2
    message_id = argv[0]

    with Session(engine) as session, ImapMailbox.from_settings() as mailbox:
        try:
            row = reflag(session, mailbox, message_id)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    print(
        f"reflagged email {row.id} from {row.from_addr!r}: "
        f"snippet is now {len(row.snippet)} chars, looks_like_travel=True"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
