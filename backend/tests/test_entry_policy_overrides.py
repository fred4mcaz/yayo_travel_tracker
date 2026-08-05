"""Round 4: curated entry-policy overrides -- human-verified ground truth that
wins over both the LLM cache and any row already in the DB.

The bug that prompted this: production told a Mexican passport holder they could
enter Indonesia visa-free. The model simply got it wrong, and decision 7 caches
forever. These tests pin the fix: an override beats a stale/wrong cached row,
never triggers a model call, and a bad override entry is skipped rather than
blanking the whole set. The last test guards the exact (ID, MX) regression
against the shipped file.
"""

import json
from pathlib import Path

from sqlmodel import Session, select

from app.models import EntryPolicy, Nationality, PermitType
from app.services import entry_policy as ep
from app.services.entry_policy import (
    OVERRIDE_SOURCE,
    cached_policy,
    get_policy,
    load_overrides,
)

from tests.test_entry_policy import FakeModel, VALID_POLICY

# The real committed file -- repo_root/data/rules/... (tests/ -> backend/ -> root)
REAL_OVERRIDES = (
    Path(__file__).resolve().parents[2] / "data" / "rules" / "entry-policy-overrides.json"
)

ID_MX_VOA = {
    "country_code": "ID",
    "nationality": "MX",
    "permit_type": "visa_on_arrival",
    "permitted_days": 30,
    "visa_required": True,
    "entry_card_required": True,
    "entry_card_name": "Indonesia e-CD",
    "summary": "Visa on arrival, 30 days.",
    "advisory": "Advisory only.",
    "checked_on": "2026-08-05",
}


def _use_overrides(tmp_path, monkeypatch, policies):
    """Point load_overrides at a temp file with these entries and clear its cache."""
    target = tmp_path / "entry-policy-overrides.json"
    target.write_text(json.dumps({"policies": policies}), encoding="utf-8")
    monkeypatch.setattr(ep, "overrides_path", lambda: target)
    load_overrides.cache_clear()
    return target


def test_override_wins_over_a_stale_cached_row(session: Session, tmp_path, monkeypatch):
    # A wrong reading already cached -- exactly the prod situation.
    session.add(
        EntryPolicy(
            country_code="ID",
            nationality=Nationality.MX,
            permit_type=PermitType.visa_free,
            permitted_days=30,
            visa_required=False,
            source_model="anthropic/claude-sonnet-5",
        )
    )
    session.commit()

    # Without an override, cached_policy returns the wrong row.
    _use_overrides(tmp_path, monkeypatch, [])
    assert cached_policy(session, "ID", Nationality.MX).permit_type == PermitType.visa_free

    # With the override, it wins -- even though a (stale) DB row exists.
    _use_overrides(tmp_path, monkeypatch, [ID_MX_VOA])
    got = cached_policy(session, "ID", Nationality.MX)
    assert got.permit_type == PermitType.visa_on_arrival
    assert got.visa_required is True
    assert got.source_model == OVERRIDE_SOURCE
    load_overrides.cache_clear()


def test_get_policy_with_an_override_makes_zero_model_calls(
    session: Session, tmp_path, monkeypatch
):
    _use_overrides(tmp_path, monkeypatch, [ID_MX_VOA])
    model = FakeModel(VALID_POLICY)
    got = get_policy(session, "ID", Nationality.MX, model)
    assert got.permit_type == PermitType.visa_on_arrival
    assert model.calls == []  # the whole point: overridden pairs never hit the model
    # And nothing competing was baked into the cache table.
    assert session.exec(select(EntryPolicy)).all() == []
    load_overrides.cache_clear()


def test_an_uncovered_pair_still_falls_through_to_the_model(
    session: Session, tmp_path, monkeypatch
):
    _use_overrides(tmp_path, monkeypatch, [ID_MX_VOA])
    # No override for (JP, US): the normal fetch-and-cache path runs untouched.
    assert cached_policy(session, "JP", Nationality.US) is None
    model = FakeModel(VALID_POLICY)
    got = get_policy(session, "JP", Nationality.US, model)
    assert got is not None
    assert len(model.calls) == 1
    assert got.id is not None  # persisted, unlike an override
    load_overrides.cache_clear()


def test_a_malformed_override_entry_is_skipped_not_fatal(tmp_path, monkeypatch):
    bad = {**ID_MX_VOA, "country_code": "SG", "permit_type": "not_a_real_permit"}
    _use_overrides(tmp_path, monkeypatch, [bad, ID_MX_VOA])
    overrides = load_overrides()
    assert ("ID", Nationality.MX) in overrides  # the good one loaded
    assert ("SG", Nationality.MX) not in overrides  # the bad one was dropped
    load_overrides.cache_clear()


def test_shipped_overrides_correct_the_indonesia_mx_bug():
    """Regression guard against the exact production bug -- run against the real
    committed file directly (the autouse fixture blanks the default path)."""
    assert REAL_OVERRIDES.exists()
    overrides = load_overrides(path=REAL_OVERRIDES)
    row = overrides.get(("ID", Nationality.MX))
    assert row is not None, "the Indonesia/MX correction must ship"
    assert row.permit_type == PermitType.visa_on_arrival
    assert row.visa_required is True
    load_overrides.cache_clear()
