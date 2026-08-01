"""Phase 6: the poll timer, and the gate that keeps it dormant.

The exit criteria, as tests:
  - Flag off  -> nothing starts, nothing connects, no credential is read.
  - Flag on, a credential missing -> a loud failure, not a silent no-op.
  - A credential VALUE never appears in a log line or an error message.

No scheduler thread is started against the real interval, and nothing here
opens a socket.
"""

import logging

import pytest

from app.config import Settings
from app.services import scheduler
from app.services.scheduler import (
    missing_credentials,
    scheduler_decision,
    start_scheduler,
)

SECRET_PW = "hunter2-app-password"
SECRET_KEY = "sk-or-secret-key"


def _settings(**kw) -> Settings:
    base = dict(
        email_ingest_enabled=False,
        imap_user="",
        imap_app_password="",
        openrouter_api_key="",
    )
    base.update(kw)
    return Settings(**base)


def _configured() -> Settings:
    return _settings(
        email_ingest_enabled=True,
        imap_user="eduardo@gmail.com",
        imap_app_password=SECRET_PW,
        openrouter_api_key=SECRET_KEY,
    )


class FakeScheduler:
    """Stands in for APScheduler so no real thread or timer is created."""

    instances: list["FakeScheduler"] = []

    def __init__(self, *a, **kw):
        self.jobs: list[dict] = []
        self.started = False
        FakeScheduler.instances.append(self)

    def add_job(self, func, trigger, **kw):
        self.jobs.append({"func": func, "trigger": trigger, **kw})

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        self.started = False


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeScheduler.instances = []
    yield


# --------------------------------------------------------------------------
# The decision -- pure, reads config, touches nothing
# --------------------------------------------------------------------------


def test_missing_credentials_lists_env_names():
    assert missing_credentials(_settings(email_ingest_enabled=True)) == [
        "YAYO_IMAP_USER",
        "YAYO_IMAP_APP_PASSWORD",
        "YAYO_OPENROUTER_API_KEY",
    ]
    assert missing_credentials(_configured()) == []


def test_decision_off_reports_no_missing_even_if_unset():
    """A disabled box is not misconfigured for lacking unused credentials."""
    should_start, missing = scheduler_decision(_settings(email_ingest_enabled=False))
    assert should_start is False
    assert missing == []


def test_decision_on_and_incomplete():
    should_start, missing = scheduler_decision(
        _settings(email_ingest_enabled=True, imap_user="x")
    )
    assert should_start is True
    assert missing == ["YAYO_IMAP_APP_PASSWORD", "YAYO_OPENROUTER_API_KEY"]


def test_decision_on_and_complete():
    assert scheduler_decision(_configured()) == (True, [])


# --------------------------------------------------------------------------
# start_scheduler -- the gate in action
# --------------------------------------------------------------------------


def test_disabled_starts_nothing(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    with caplog.at_level(logging.INFO, logger="yayo.scheduler"):
        result = start_scheduler(engine=object(), settings=_settings())

    assert result is None
    assert FakeScheduler.instances == []  # nothing was even constructed
    assert "disabled" in caplog.text.lower()


def test_enabled_but_missing_credentials_raises_loudly():
    with pytest.raises(RuntimeError) as exc:
        start_scheduler(engine=object(), settings=_settings(email_ingest_enabled=True))
    msg = str(exc.value)
    assert "YAYO_IMAP_USER" in msg
    assert "YAYO_OPENROUTER_API_KEY" in msg


def test_enabled_and_configured_starts_the_poller(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    with caplog.at_level(logging.INFO, logger="yayo.scheduler"):
        result = start_scheduler(engine=object(), settings=_configured())

    assert isinstance(result, FakeScheduler)
    assert result.started is True
    job = result.jobs[0]
    assert job["trigger"] == "interval"
    assert job["minutes"] == scheduler.POLL_INTERVAL_MINUTES


def test_no_credential_value_is_ever_logged_or_raised(monkeypatch, caplog):
    """The secrets must not leak into logs or error text."""
    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    with caplog.at_level(logging.DEBUG):
        start_scheduler(engine=object(), settings=_configured())
    assert SECRET_PW not in caplog.text
    assert SECRET_KEY not in caplog.text

    # And on the failure path, the message names vars, not values.
    with pytest.raises(RuntimeError) as exc:
        start_scheduler(
            engine=object(),
            settings=_settings(email_ingest_enabled=True, imap_app_password=SECRET_PW),
        )
    assert SECRET_PW not in str(exc.value)


# --------------------------------------------------------------------------
# A cycle survives a transient failure
# --------------------------------------------------------------------------


def test_safe_cycle_swallows_errors(monkeypatch, caplog):
    def boom(_engine):
        raise ConnectionError("mail server said no")

    monkeypatch.setattr(scheduler, "run_poll_cycle", boom)
    with caplog.at_level(logging.ERROR, logger="yayo.scheduler"):
        scheduler._safe_cycle(object())  # must not raise

    assert "failed" in caplog.text.lower()
