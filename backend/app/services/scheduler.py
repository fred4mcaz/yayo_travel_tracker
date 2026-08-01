"""The email poll: fetch, filter, extract, on a timer -- and the gate that
keeps all of it dormant until it is deliberately switched on.

The gate is the whole point. With `YAYO_EMAIL_INGEST_ENABLED=false` (the
default) nothing here starts, nothing connects to a mail server, and no
credential is read or logged. The moment the flag is on, missing credentials
are a loud boot failure rather than a silent no-op -- flipping the flag is a
statement that ingest should run, so a half-configured box should say so.

The decision is a pure function so it can be tested without a scheduler, a
socket, or an API key.
"""

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session

from app.config import Settings
from app.services.email_ingest import run_ingest
from app.services.extraction import OpenRouterModel, run_extractions

log = logging.getLogger("yayo.scheduler")

POLL_INTERVAL_MINUTES = 10

# The credentials ingest cannot run without. Names only -- values are never
# named anywhere in this module.
REQUIRED_CREDENTIALS = (
    ("imap_user", "YAYO_IMAP_USER"),
    ("imap_app_password", "YAYO_IMAP_APP_PASSWORD"),
    ("openrouter_api_key", "YAYO_OPENROUTER_API_KEY"),
)


def missing_credentials(settings: Settings) -> list[str]:
    """The env-var names that must be set but are not. Empty means ready."""
    return [
        env_name
        for attr, env_name in REQUIRED_CREDENTIALS
        if not getattr(settings, attr)
    ]


def scheduler_decision(settings: Settings) -> tuple[bool, list[str]]:
    """(should_start, missing). Pure -- reads config, touches nothing.

    When the flag is off, `missing` is empty and unread: a disabled box is not
    misconfigured for lacking credentials it was never going to use.
    """
    if not settings.email_ingest_enabled:
        return (False, [])
    return (True, missing_credentials(settings))


def run_poll_cycle(engine) -> dict:
    """One pass: fetch and filter new mail, then extract the candidates."""
    with Session(engine) as session:
        ingest = run_ingest(session)
        model = OpenRouterModel.from_settings()
        extraction = run_extractions(session, model)
    return {"ingest": ingest, "extraction": extraction}


def _safe_cycle(engine) -> None:
    """A cycle that never lets a transient failure kill the scheduler thread.

    A mail server hiccup or a rate-limited API call should mean "try again in
    ten minutes", not "ingestion is dead until the next deploy".
    """
    try:
        run_poll_cycle(engine)
    except Exception:  # noqa: BLE001 -- the whole job must survive any failure
        log.exception("email poll cycle failed; will retry next interval")


def start_scheduler(engine, settings: Settings) -> Optional[BackgroundScheduler]:
    """Start the poller if configured, or explain why it did not.

    Returns the running scheduler, or None when ingest is disabled. Raises
    RuntimeError when ingest is enabled but a credential is missing -- a
    deliberate boot failure, so the mistake is seen immediately rather than
    discovered as silence.
    """
    should_start, missing = scheduler_decision(settings)

    if not should_start:
        log.info(
            "email ingest disabled (YAYO_EMAIL_INGEST_ENABLED=false); "
            "scheduler not started"
        )
        return None

    if missing:
        raise RuntimeError(
            "email ingest is enabled but these are unset: "
            + ", ".join(missing)
            + ". Set them in deploy/.env or turn YAYO_EMAIL_INGEST_ENABLED off."
        )

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _safe_cycle,
        "interval",
        minutes=POLL_INTERVAL_MINUTES,
        args=[engine],
        id="email_poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "email ingest scheduler started; polling every %d minutes",
        POLL_INTERVAL_MINUTES,
    )
    return scheduler
