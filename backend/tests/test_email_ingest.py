"""Phase 1: fetching mail without ever fetching it twice.

No socket is opened here. The mail server is a Protocol and these tests hand in
a fake that reproduces the one IMAP behaviour that actually bites -- see
FakeMailbox.fetch_after.
"""

from datetime import datetime

from sqlmodel import Session, select

from app.models import EmailMessage
from app.services.email_filter import load_rules
from app.services.email_ingest import (
    UIDVALIDITY_KEY,
    WATERMARK_KEY,
    IncomingEmail,
    _html_to_text,
    ingest_once,
)
from app.services.settings import get_setting


def _email(uid: int, message_id: str = "", body: str = "Your booking") -> IncomingEmail:
    return IncomingEmail(
        uid=uid,
        message_id=message_id or f"<{uid}@mail.example>",
        from_addr="noreply@hotel.example",
        subject=f"Confirmation {uid}",
        received_at=datetime(2026, 8, 1, 9, 30),
        body=body,
    )


class FakeMailbox:
    """A mailbox that behaves like IMAP, including where IMAP is surprising."""

    def __init__(self, messages, validity: str = "1", newest: int | None = None):
        self.messages = sorted(messages, key=lambda m: m.uid)
        self.validity = validity
        self._newest = newest
        self.fetched_from: list[int] = []

    def uid_validity(self) -> str:
        return self.validity

    def newest_uid(self) -> int:
        if self._newest is not None:
            return self._newest
        return max((m.uid for m in self.messages), default=0)

    def fetch_after(self, uid: int):
        self.fetched_from.append(uid)
        above = [m for m in self.messages if m.uid > uid]
        if not above and self.messages:
            # The quirk: in a `n:*` range the endpoints are an unordered pair,
            # so asking for messages above the newest still returns the newest.
            return [self.messages[-1]]
        return above


def _stored(session: Session) -> list[EmailMessage]:
    return list(session.exec(select(EmailMessage).order_by(EmailMessage.imap_uid)).all())


# --------------------------------------------------------------------------
# The no-backfill guarantee
# --------------------------------------------------------------------------


def test_first_run_baselines_and_ingests_nothing(session: Session):
    """He chose "going forward only". A first run must not trawl the inbox."""
    mailbox = FakeMailbox([_email(uid) for uid in (10, 11, 12)])

    result = ingest_once(session, mailbox)

    assert result["baselined"] is True
    assert result["reason"] == "first_run"
    assert result["ingested"] == 0
    assert _stored(session) == []
    assert get_setting(session, WATERMARK_KEY) == "12"
    assert get_setting(session, UIDVALIDITY_KEY) == "1"
    # Nothing was even read.
    assert mailbox.fetched_from == []


def test_second_run_ingests_only_above_the_watermark(session: Session):
    mailbox = FakeMailbox([_email(uid) for uid in (10, 11, 12)])
    ingest_once(session, mailbox)

    mailbox.messages.append(_email(13))
    mailbox.messages.append(_email(14))
    result = ingest_once(session, mailbox)

    assert result["ingested"] == 2
    assert [m.imap_uid for m in _stored(session)] == [13, 14]
    assert get_setting(session, WATERMARK_KEY) == "14"


def test_running_twice_over_the_same_mailbox_is_idempotent(session: Session):
    mailbox = FakeMailbox([_email(10)])
    ingest_once(session, mailbox)
    mailbox.messages.append(_email(11))

    first = ingest_once(session, mailbox)
    second = ingest_once(session, mailbox)

    assert first["ingested"] == 1
    assert second["ingested"] == 0
    assert len(_stored(session)) == 1


def test_message_above_watermark_is_not_refetched_by_the_range_quirk(
    session: Session,
):
    """`n:*` returns the newest message even when nothing is above n.

    Without the client-side guard this stores the same message on every poll,
    forever.
    """
    mailbox = FakeMailbox([_email(10), _email(11)])
    ingest_once(session, mailbox)
    ingest_once(session, mailbox)

    result = ingest_once(session, mailbox)

    # The fake did hand back message 11 each time; it was rejected each time.
    assert mailbox.fetched_from == [11, 11]
    assert result["ingested"] == 0
    assert _stored(session) == []


# --------------------------------------------------------------------------
# UIDs are only meaningful under the UIDVALIDITY that issued them
# --------------------------------------------------------------------------


def test_uidvalidity_change_rebaselines_instead_of_reingesting(session: Session):
    mailbox = FakeMailbox([_email(uid) for uid in (10, 11)])
    ingest_once(session, mailbox)
    mailbox.messages.append(_email(12))
    ingest_once(session, mailbox)
    assert len(_stored(session)) == 1

    # The server reissued UIDs from scratch: the stored watermark now points at
    # an unrelated message, so the whole folder looks new.
    mailbox.validity = "2"
    mailbox.messages = [_email(uid, message_id=f"<new-{uid}@mail.example>") for uid in (1, 2, 3)]

    result = ingest_once(session, mailbox)

    assert result["baselined"] is True
    assert result["reason"] == "uidvalidity_changed"
    assert result["ingested"] == 0
    assert len(_stored(session)) == 1  # unchanged -- nothing was re-ingested
    assert get_setting(session, WATERMARK_KEY) == "3"
    assert get_setting(session, UIDVALIDITY_KEY) == "2"


# --------------------------------------------------------------------------
# Identity, ordering, batching
# --------------------------------------------------------------------------


def test_same_message_under_a_new_uid_is_stored_once(session: Session):
    """A folder move reissues the UID. Message-ID is the identity."""
    mailbox = FakeMailbox([_email(10)])
    ingest_once(session, mailbox)

    mailbox.messages.append(_email(11, message_id="<moved@mail.example>"))
    ingest_once(session, mailbox)
    mailbox.messages.append(_email(12, message_id="<moved@mail.example>"))
    result = ingest_once(session, mailbox)

    assert result["ingested"] == 0
    assert result["skipped"] == 1
    assert len(_stored(session)) == 1


def test_limit_leaves_the_remainder_above_the_watermark(session: Session):
    mailbox = FakeMailbox([_email(10)])
    ingest_once(session, mailbox)
    for uid in range(11, 16):
        mailbox.messages.append(_email(uid))

    first = ingest_once(session, mailbox, limit=2)

    assert first["ingested"] == 2
    assert get_setting(session, WATERMARK_KEY) == "12"

    second = ingest_once(session, mailbox, limit=2)

    assert second["ingested"] == 2
    assert [m.imap_uid for m in _stored(session)] == [11, 12, 13, 14]


def test_out_of_order_delivery_stops_rather_than_stranding_a_message(
    session: Session,
):
    """The watermark moves as we go, so anything below it is lost.

    A mailbox that yields descending would have its lower UIDs skipped
    permanently. Stopping early means they are simply picked up next run.
    """

    class DescendingMailbox(FakeMailbox):
        def fetch_after(self, uid: int):
            return sorted(super().fetch_after(uid), key=lambda m: m.uid, reverse=True)

    mailbox = DescendingMailbox([_email(10)])
    ingest_once(session, mailbox)
    for uid in (11, 12, 13):
        mailbox.messages.append(_email(uid))

    result = ingest_once(session, mailbox)

    assert result["ingested"] == 1
    assert [m.imap_uid for m in _stored(session)] == [13]
    assert get_setting(session, WATERMARK_KEY) == "13"


def test_snippet_is_collapsed_and_truncated(session: Session):
    mailbox = FakeMailbox([_email(10)])
    ingest_once(session, mailbox)
    mailbox.messages.append(_email(11, body="  Hanoi\n\n\tSofitel   Legend  " + "x" * 500))

    ingest_once(session, mailbox)

    snippet = _stored(session)[0].snippet
    assert snippet.startswith("Hanoi Sofitel Legend x")
    assert len(snippet) == 400


def test_an_unlisted_sender_is_never_a_candidate(session: Session):
    """The pre-filter (phase 2) runs at store time. `_email` uses an unlisted
    sender, so even with booking wording it is not marked -- proof that the
    ingester defers the decision to the filter rather than defaulting to yes."""
    mailbox = FakeMailbox([_email(10)])
    ingest_once(session, mailbox)
    mailbox.messages.append(_email(11))

    ingest_once(session, mailbox)

    assert [m.looks_like_travel for m in _stored(session)] == [False]


# --------------------------------------------------------------------------
# HTML-only bodies (redBus and friends send no text/plain part at all)
# --------------------------------------------------------------------------


def test_html_to_text_drops_style_and_script_but_keeps_content():
    html = (
        "<html><head><style>.a{color:red}\nbody{font-size:2px}</style>"
        "<script>track('open')</script></head>"
        "<body><p>Your PNR is <b>AB12CD</b>.</p>"
        "<p>Fare&nbsp;&amp;&nbsp;taxes: SGD&nbsp;20</p></body></html>"
    )
    text = _html_to_text(html)
    assert "color:red" not in text
    assert "track(" not in text
    assert "Your PNR is AB12CD" in text
    assert "Fare & taxes: SGD 20" in text


def test_html_to_text_collapses_whitespace():
    assert _html_to_text("<p>Hanoi</p>\n\n<p>\tSofitel   Legend</p>") == "Hanoi Sofitel Legend"


def test_html_to_text_of_empty_input_is_empty():
    assert _html_to_text("") == ""


_REDBUS_HTML = """
<html>
<head><style>.wrap { padding: 4px; } .foot::before { content: "unsubscribe"; }</style></head>
<body>
  <p>Your ferry ticket is confirmed.</p>
  <p>PNR: RB998877</p>
  <p>Batam &rarr; Malaysia, 04 Aug 2026</p>
</body>
</html>
"""


def test_html_only_message_yields_a_non_empty_body_and_snippet(session: Session):
    """The fallback the ingester applies before storing: no text/plain part, so
    the body -- and the snippet derived from it -- come from the HTML."""
    body = _html_to_text(_REDBUS_HTML)
    assert body  # non-empty: this is the whole point of the fallback

    mailbox = FakeMailbox([_email(10)])
    ingest_once(session, mailbox)
    mailbox.messages.append(
        _email(
            11,
            body=body,
        )
    )
    ingest_once(session, mailbox)

    stored = _stored(session)[-1]
    assert stored.snippet
    assert "PNR: RB998877" in stored.snippet


def test_redbus_shaped_html_only_email_classifies_as_a_candidate(session: Session):
    """The regression this whole phase exists for: an HTML-only redBus
    confirmation, with no text/plain part, must be flagged for review once its
    body is run through the HTML fallback -- not silently dropped."""
    load_rules.cache_clear()
    body = _html_to_text(_REDBUS_HTML)

    mailbox = FakeMailbox([_email(10)])
    ingest_once(session, mailbox)
    mailbox.messages.append(
        IncomingEmail(
            uid=11,
            message_id="<redbus-1@redbus.sg>",
            from_addr="ticketmaster@redbus.sg",
            subject="Your ferry booking confirmation",
            received_at=datetime(2026, 8, 4, 14, 41),
            body=body,
        )
    )
    ingest_once(session, mailbox)

    stored = _stored(session)[-1]
    assert stored.looks_like_travel is True
