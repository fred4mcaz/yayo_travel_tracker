"""Phase 2: recovering one already-stored message the automatic filter never
caught -- the redBus ferry's actual failure mode.

No socket opened here either; the mailbox is a small fake exposing exactly
the one method this task needs.
"""

from datetime import datetime

import pytest
from sqlmodel import Session

from app.models import EmailMessage
from app.services.email_ingest import IncomingEmail, _html_to_text
from app.tasks.reflag_message import reflag


class FakeMessageLookup:
    def __init__(self, emails: dict):
        self._emails = emails

    def fetch_by_message_id(self, message_id: str):
        return self._emails.get(message_id)


def _stored_ferry(session: Session) -> EmailMessage:
    row = EmailMessage(
        imap_uid=41,
        message_id="<ferry-1@redbus.sg>",
        from_addr="ticketmaster@redbus.sg",
        subject="Your ferry booking confirmation",
        received_at=datetime(2026, 8, 4, 14, 41),
        snippet="",
        looks_like_travel=False,
        processed_at=datetime(2026, 8, 4, 15, 0),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_reflag_updates_snippet_flag_and_clears_processed_at(session: Session):
    stored = _stored_ferry(session)
    html = "<style>.a{}</style><p>PNR RB998877, Batam to Malaysia</p>"
    mailbox = FakeMessageLookup(
        {
            stored.message_id: IncomingEmail(
                uid=99,
                message_id=stored.message_id,
                from_addr=stored.from_addr,
                subject=stored.subject,
                received_at=stored.received_at,
                body=_html_to_text(html),
            )
        }
    )

    result = reflag(session, mailbox, stored.message_id)

    assert result.id == stored.id
    assert result.looks_like_travel is True
    assert result.processed_at is None
    assert "PNR RB998877" in result.snippet
    # Refreshed from the live re-fetch -- may differ from the stale stored UID.
    assert result.imap_uid == 99


def test_reflag_missing_stored_row_raises(session: Session):
    """Nothing to recover if the message was never ingested at all."""
    mailbox = FakeMessageLookup({})
    with pytest.raises(LookupError):
        reflag(session, mailbox, "<never-stored@example.com>")


def test_reflag_message_not_found_on_mailbox_leaves_the_row_untouched(
    session: Session,
):
    """Deleted, moved, or a typo'd message-id -- either way, don't half-apply."""
    stored = _stored_ferry(session)
    mailbox = FakeMessageLookup({})

    with pytest.raises(LookupError):
        reflag(session, mailbox, stored.message_id)

    session.refresh(stored)
    assert stored.looks_like_travel is False
    assert stored.snippet == ""
