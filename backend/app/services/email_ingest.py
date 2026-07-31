"""Pulling new mail into email_message.

**Going forward only.** The first run records where the mailbox is now and
ingests nothing. There is no historical backfill and there should not be one --
the point is to catch bookings as they arrive, not to trawl years of inbox.

Nothing here decides whether a message is interesting (stage 8 phase 2) and
nothing here talks to an LLM (phase 3). This module's whole job is: fetch what
is new, store it once, and never fetch it twice.

The mail server sits behind a Protocol so the tests can hand in a fake mailbox.
No test in this file's suite opens a socket.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator, Optional, Protocol

from sqlmodel import Session, select

from app.config import get_settings
from app.models import EmailMessage
from app.services.email_filter import classify
from app.services.settings import get_setting, set_setting

log = logging.getLogger("yayo.email")

# Watermark: the highest UID already accounted for. UIDs are only meaningful
# within one folder of one mailbox incarnation, so the UIDVALIDITY they were
# issued under is stored beside them.
WATERMARK_KEY = "imap_last_uid"
UIDVALIDITY_KEY = "imap_uidvalidity"

SNIPPET_CHARS = 400


@dataclass(frozen=True)
class IncomingEmail:
    """One message, reduced to what the pipeline actually reads."""

    uid: int
    message_id: str
    from_addr: str
    subject: str
    received_at: Optional[datetime]
    body: str


class Mailbox(Protocol):
    """What the ingester needs from a mail server.

    `fetch_after` must yield in ascending UID order. The ingester advances the
    watermark as it goes, so a message delivered out of order would be stranded
    below the mark and never seen again; it stops early rather than risk that.
    """

    def uid_validity(self) -> str:
        ...

    def newest_uid(self) -> int:
        ...

    def fetch_after(self, uid: int) -> Iterable[IncomingEmail]:
        ...


def _snippet(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip()[:SNIPPET_CHARS]


def _baseline(session: Session, mailbox: Mailbox, validity: str, reason: str) -> dict:
    """Record where the mailbox is now, without reading any of it."""
    newest = mailbox.newest_uid()
    set_setting(session, UIDVALIDITY_KEY, validity)
    set_setting(session, WATERMARK_KEY, str(newest))
    session.commit()
    log.info("email ingest baselined at uid %d (%s)", newest, reason)
    return {
        "baselined": True,
        "reason": reason,
        "ingested": 0,
        "skipped": 0,
        "watermark": newest,
    }


def ingest_once(session: Session, mailbox: Mailbox, *, limit: int = 200) -> dict:
    """Fetch everything above the watermark and store it. Returns a summary.

    Safe to run repeatedly: messages are keyed by Message-ID, so a message that
    somehow arrives twice is stored once.
    """
    validity = mailbox.uid_validity()
    stored_validity = get_setting(session, UIDVALIDITY_KEY)

    if stored_validity is None:
        # First ever run. This is the no-backfill guarantee.
        return _baseline(session, mailbox, validity, "first_run")

    if stored_validity != validity:
        # The server reissued UIDs from scratch, so the old watermark now points
        # at an unrelated message. Re-baseline; re-ingesting the whole folder
        # would be the other option and is exactly what he did not ask for.
        return _baseline(session, mailbox, validity, "uidvalidity_changed")

    watermark = int(get_setting(session, WATERMARK_KEY) or 0)
    ingested = skipped = 0
    cursor = watermark

    for email in mailbox.fetch_after(watermark):
        # IMAP quirk, not a redundant check: in a `n:*` range the two endpoints
        # are an unordered pair, so a folder whose highest UID is below `n`
        # still returns that message. Filtering server-side is not enough.
        if email.uid <= watermark:
            continue
        if email.uid <= cursor:
            log.warning(
                "mailbox yielded uid %d after %d; stopping to avoid stranding it",
                email.uid,
                cursor,
            )
            break

        if _store(session, email):
            ingested += 1
        else:
            skipped += 1

        cursor = email.uid
        if ingested + skipped >= limit:
            # Leave the rest above the watermark; the next run picks them up.
            break

    if cursor > watermark:
        set_setting(session, WATERMARK_KEY, str(cursor))
    session.commit()

    if ingested or skipped:
        log.info(
            "email ingest: %d new, %d already seen, watermark now %d",
            ingested,
            skipped,
            cursor,
        )
    return {
        "baselined": False,
        "reason": None,
        "ingested": ingested,
        "skipped": skipped,
        "watermark": cursor,
    }


def _store(session: Session, email: IncomingEmail) -> bool:
    """Persist one message. False if it was already stored.

    Message-ID is the identity, not the UID: the same message can be re-fetched
    under a new UID after a folder move, and storing it twice would put it
    through extraction twice.
    """
    if email.message_id:
        existing = session.exec(
            select(EmailMessage).where(EmailMessage.message_id == email.message_id)
        ).first()
        if existing is not None:
            return False

    # The local pre-filter runs here, at store time, entirely on the box. It
    # only sets a flag; nothing leaves the machine until an operator with the
    # flag set is picked up for extraction (phase 3).
    verdict = classify(email.from_addr, email.subject, email.body)
    if not verdict:
        log.debug("uid %d not a travel candidate: %s", email.uid, verdict.reason)

    session.add(
        EmailMessage(
            imap_uid=email.uid,
            message_id=email.message_id,
            from_addr=email.from_addr,
            subject=email.subject,
            received_at=email.received_at,
            snippet=_snippet(email.body),
            looks_like_travel=verdict.is_candidate,
        )
    )
    return True


# --------------------------------------------------------------------------
# The real mail server
# --------------------------------------------------------------------------


class ImapMailbox:
    """A Gmail folder over IMAP+TLS.

    Constructed per run and used as a context manager; the connection is not
    held open between polls.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        folder: str = "INBOX",
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._folder = folder
        self._box = None

    @classmethod
    def from_settings(cls) -> "ImapMailbox":
        s = get_settings()
        if not s.imap_user or not s.imap_app_password:
            raise RuntimeError(
                "IMAP credentials are not configured. Set YAYO_IMAP_USER and "
                "YAYO_IMAP_APP_PASSWORD in deploy/.env."
            )
        return cls(s.imap_host, s.imap_port, s.imap_user, s.imap_app_password)

    def __enter__(self) -> "ImapMailbox":
        from imap_tools import MailBox

        self._box = MailBox(self._host, port=self._port).login(
            self._user, self._password, initial_folder=self._folder
        )
        return self

    def __exit__(self, *exc) -> None:
        if self._box is not None:
            self._box.logout()
            self._box = None

    @property
    def _mailbox(self):
        if self._box is None:
            raise RuntimeError("ImapMailbox must be used as a context manager")
        return self._box

    def uid_validity(self) -> str:
        return str(self._mailbox.folder.status()["UIDVALIDITY"])

    def newest_uid(self) -> int:
        # UIDNEXT is the UID the *next* message will get, so the highest that
        # currently exists is at most one below it. Using it as the baseline
        # means an empty folder baselines at 0 rather than failing.
        return max(int(self._mailbox.folder.status()["UIDNEXT"]) - 1, 0)

    def fetch_after(self, uid: int) -> Iterator[IncomingEmail]:
        from imap_tools import AND, UidRange

        criteria = AND(uid=UidRange(str(uid + 1), "*"))
        for msg in self._mailbox.fetch(criteria, mark_seen=False, bulk=True):
            if not msg.uid:
                continue
            yield IncomingEmail(
                uid=int(msg.uid),
                message_id=_header(msg, "message-id"),
                from_addr=msg.from_ or "",
                subject=msg.subject or "",
                received_at=msg.date,
                body=msg.text or "",
            )


def _header(msg, name: str) -> str:
    values = msg.headers.get(name, ())
    return values[0].strip() if values else ""


def run_ingest(session: Session) -> dict:
    """One polling cycle against the configured mailbox."""
    with ImapMailbox.from_settings() as mailbox:
        return ingest_once(session, mailbox)
