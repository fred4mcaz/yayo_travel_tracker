"""Phase 2: materializing requirements from a policy, and the derived
readiness reading.

Exercises services.trips.sync_requirements and trip_readiness directly with a
FakeModel (same shape as test_entry_policy.py's), plus the trip mutation
endpoints that wire sync_requirements in -- creating a stay or changing the
passport on a CountryEntry must reconcile the checklist without a second
explicit call.
"""

from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import Nationality, Requirement, RequirementKind, RequirementStatus
from app.services.trips import sync_requirements, trip_readiness

TODAY = date.today()


class FakePolicyModel:
    policy_model = "fake/model"

    def __init__(self, readings: dict[tuple[str, str], dict]):
        # readings keyed by (country_code, nationality)
        self._readings = readings
        self.calls: list[tuple[str, str, str]] = []

    def assess_entry_policy(self, country_code, country_name, nationality):
        self.calls.append((country_code, country_name, nationality))
        return self._readings.get((country_code, nationality))


VISA_FREE_JAPAN = {
    "permit_type": "visa_free",
    "permitted_days": 90,
    "visa_required": False,
    "entry_card_required": False,
    "entry_card_name": None,
    "eta_required": False,
    "insurance_required": False,
    "vaccination_required": False,
    "onward_ticket_required": False,
    "summary": "Visa-free, 90 days.",
    "advisory": "Border rules change without notice -- verify before you fly.",
}

VOA_INDONESIA = {
    "permit_type": "visa_on_arrival",
    "permitted_days": 30,
    "visa_required": True,
    "entry_card_required": True,
    "entry_card_name": "Indonesia e-CD",
    "eta_required": False,
    "insurance_required": False,
    "vaccination_required": False,
    "onward_ticket_required": False,
    "summary": "Visa-on-arrival, 30 days.",
    "advisory": "Border rules change without notice -- verify before you fly.",
}

VISA_REQUIRED_INDONESIA_MX = {
    **VOA_INDONESIA,
    "permit_type": "visa",
    "summary": "Visa required in advance, 30 days.",
}


def _mk_trip(client) -> int:
    r = client.post("/api/trips", json={})
    assert r.status_code == 201
    return r.json()["id"]


def _mk_stay(client, trip_id, country_code="jp", **overrides) -> dict:
    payload = {
        "country_code": country_code,
        "city": "Tokyo",
        "check_in": str(TODAY + timedelta(days=10)),
        "check_out": str(TODAY + timedelta(days=15)),
        "hotel_name": "Park Hyatt",
    }
    payload.update(overrides)
    r = client.post(f"/api/trips/{trip_id}/stays", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------
# sync_requirements + trip_readiness, service-level
# --------------------------------------------------------------------------


def test_visa_free_country_gets_no_visa_row_and_reads_ready(session: Session, client):
    from app.models import Trip

    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="jp")
    trip = session.get(Trip, trip_id)
    model = FakePolicyModel({("JP", "US"): VISA_FREE_JAPAN})

    rows = sync_requirements(session, trip, model)
    assert rows == []
    assert (
        session.exec(
            select(Requirement).where(Requirement.trip_id == trip_id)
        ).all()
        == []
    )

    readiness = trip_readiness(session, trip)
    assert readiness["state"] == "ready"
    assert readiness["permit"] == "visa_free"
    assert readiness["checklist"] == []
    assert readiness["arrival_card"] is None


def test_visa_on_arrival_country_gets_visa_and_entry_card_rows_and_reads_action(
    session: Session, client
):
    from app.models import Trip

    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")
    trip = session.get(Trip, trip_id)
    model = FakePolicyModel({("ID", "US"): VOA_INDONESIA})

    rows = sync_requirements(session, trip, model)
    kinds = {r.kind for r in rows}
    assert kinds == {RequirementKind.visa, RequirementKind.entry_card}
    assert all(r.status == RequirementStatus.todo for r in rows)

    readiness = trip_readiness(session, trip)
    assert readiness["state"] == "action"
    assert readiness["permit"] == "visa_on_arrival"
    assert {c["kind"] for c in readiness["checklist"]} == {"visa", "entry_card"}
    assert readiness["arrival_card"]["confirmed"] is False
    assert readiness["arrival_card"]["name"] == "Indonesia e-CD"

    # Idempotent -- running it again with the same model changes nothing and
    # makes no further model calls beyond the single fetch.
    sync_requirements(session, trip, model)
    assert len(model.calls) == 1
    rows_again = session.exec(
        select(Requirement).where(Requirement.trip_id == trip_id)
    ).all()
    assert len(rows_again) == 2

    # Once the arrival card is approved, readiness flips to ready.
    entry_card = next(r for r in rows if r.kind == RequirementKind.entry_card)
    entry_card.status = RequirementStatus.approved
    session.add(entry_card)
    visa = next(r for r in rows if r.kind == RequirementKind.visa)
    visa.status = RequirementStatus.approved
    session.add(visa)
    session.commit()
    assert trip_readiness(session, trip)["state"] == "ready"


def test_undated_or_countryless_trip_reads_na_with_no_rows(session: Session, client):
    from app.models import Trip

    trip_id = _mk_trip(client)
    trip = session.get(Trip, trip_id)
    model = FakePolicyModel({})

    rows = sync_requirements(session, trip, model)
    assert rows == []
    assert model.calls == []
    assert trip_readiness(session, trip) == {
        "state": "na",
        "passport": None,
        "is_default_us": False,
        "permit": None,
        "permitted_days": None,
        "checklist": [],
        "arrival_card": None,
        "advisory": "",
        "checked_on": None,
        "alternate_passport_hint": None,
        "discrepancy": None,
    }


def test_unknown_policy_leaves_existing_rows_untouched(session: Session, client):
    """No cached policy and no model to fetch one: readiness is `unknown`, and
    a reconciliation pass must not delete rows it can't justify deleting."""
    from app.models import Trip

    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")
    trip = session.get(Trip, trip_id)

    # First materialize with a real reading...
    sync_requirements(session, trip, FakePolicyModel({("ID", "US"): VOA_INDONESIA}))
    before = session.exec(
        select(Requirement).where(Requirement.trip_id == trip_id)
    ).all()
    assert len(before) == 2

    # ...then reconcile again with no model at all (simulating a later save on
    # an unconfigured box, or one for a different country not yet cached).
    rows = sync_requirements(session, trip, model=None)
    assert len(rows) == 2
    after = session.exec(
        select(Requirement).where(Requirement.trip_id == trip_id)
    ).all()
    assert len(after) == 2


def test_never_touches_a_row_the_user_or_email_advanced(session: Session, client):
    from app.models import Trip

    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")
    trip = session.get(Trip, trip_id)
    model = FakePolicyModel({("ID", "US"): VOA_INDONESIA})
    sync_requirements(session, trip, model)

    entry_card = session.exec(
        select(Requirement)
        .where(Requirement.trip_id == trip_id)
        .where(Requirement.kind == RequirementKind.entry_card)
    ).first()
    entry_card.status = RequirementStatus.approved
    session.add(entry_card)
    session.commit()

    # Even a policy that no longer requires an entry card must not retract an
    # already-advanced row.
    no_entry_card = {**VOA_INDONESIA, "entry_card_required": False, "entry_card_name": None}
    model2 = FakePolicyModel({("ID", "MX"): no_entry_card})
    # Different nationality cache key so this exercises "policy still says
    # required" being irrelevant once the row moved past todo -- re-fetch the
    # same (ID, US) reading (still cached, no new call) and confirm the
    # approved row survives reconciliation.
    sync_requirements(session, trip, model2)
    still_there = session.exec(
        select(Requirement)
        .where(Requirement.trip_id == trip_id)
        .where(Requirement.kind == RequirementKind.entry_card)
    ).first()
    assert still_there is not None
    assert still_there.status == RequirementStatus.approved


# --------------------------------------------------------------------------
# Passport flip reconciliation
# --------------------------------------------------------------------------


def test_passport_flip_reconciles_rows_and_preserves_advanced_status(
    session: Session, client
):
    from app.models import CountryEntry, Passport, Trip

    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")
    trip = session.get(Trip, trip_id)

    mx_passport = Passport(nationality=Nationality.MX)
    session.add(mx_passport)
    session.commit()
    session.refresh(mx_passport)

    model = FakePolicyModel(
        {
            ("ID", "US"): VOA_INDONESIA,
            ("ID", "MX"): VISA_REQUIRED_INDONESIA_MX,
        }
    )

    # US default: visa + entry card.
    sync_requirements(session, trip, model)
    visa_row = session.exec(
        select(Requirement)
        .where(Requirement.trip_id == trip_id)
        .where(Requirement.kind == RequirementKind.visa)
    ).first()
    visa_row.status = RequirementStatus.submitted
    session.add(visa_row)
    session.commit()

    entry = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == trip_id)
    ).first()
    entry.passport_id = mx_passport.id
    session.add(entry)
    session.commit()
    session.refresh(trip)

    sync_requirements(session, trip, model)
    readiness = trip_readiness(session, trip)
    assert readiness["passport"] == "MX"
    assert readiness["permit"] == "visa"

    rows = {
        r.kind: r
        for r in session.exec(
            select(Requirement).where(Requirement.trip_id == trip_id)
        ).all()
    }
    # Still visa + entry_card required under MX; the user-advanced status on
    # the (still-existing, same-kind) visa row is untouched by the flip.
    assert rows[RequirementKind.visa].status == RequirementStatus.submitted
    assert rows[RequirementKind.entry_card].status == RequirementStatus.todo


# --------------------------------------------------------------------------
# API wiring -- endpoints call sync_requirements automatically
# --------------------------------------------------------------------------


def test_creating_a_stay_materializes_requirements_via_the_api(client, monkeypatch):
    import app.api.trips as trips_api

    model = FakePolicyModel({("ID", "US"): VOA_INDONESIA})
    monkeypatch.setattr(trips_api, "policy_model_or_none", lambda: model)

    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id, country_code="id")

    assert detail["readiness"]["state"] == "action"
    kinds = {r["kind"] for r in detail["requirements"] if r["source"] == "system"}
    assert kinds == {"visa", "entry_card"}


def test_list_trips_includes_compact_readiness(client, monkeypatch):
    import app.api.trips as trips_api

    model = FakePolicyModel({("ID", "US"): VOA_INDONESIA})
    monkeypatch.setattr(trips_api, "policy_model_or_none", lambda: model)

    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")

    listing = client.get("/api/trips").json()
    row = next(t for t in listing if t["id"] == trip_id)
    assert row["readiness"]["state"] == "action"
    assert "checklist" not in row["readiness"]
