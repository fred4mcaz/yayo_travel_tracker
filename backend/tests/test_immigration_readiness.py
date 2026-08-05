"""Phase 2: materializing requirements from a policy, and the derived
readiness reading.

Exercises services.trips.sync_requirements and trip_readiness directly with a
FakeModel (same shape as test_entry_policy.py's), plus the trip mutation
endpoints that wire sync_requirements in -- creating a stay or changing the
passport on a CountryEntry must reconcile the checklist without a second
explicit call.
"""

import json
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.models import (
    EmailMessage,
    Extraction,
    ExtractionKind,
    ExtractionStatus,
    Leg,
    Nationality,
    Requirement,
    RequirementKind,
    RequirementStatus,
    Trip,
)
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
    assert readiness["arrival_card"]["state"] == "none"
    assert readiness["arrival_card"]["name"] == "Indonesia e-CD"
    assert readiness["onward_ticket"] is None

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
        "onward_ticket": None,
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
    # onward_ticket rides along in the compact set too (None here -- VoA
    # Indonesia doesn't require one), so the card badge can speak to it.
    assert "onward_ticket" in row["readiness"]


# --------------------------------------------------------------------------
# Phase 7: automated arrival-card (3-state) and onward-ticket readings
# --------------------------------------------------------------------------

ONWARD_REQUIRED_THAILAND = {
    "permit_type": "visa_free",
    "permitted_days": 30,
    "visa_required": False,
    "entry_card_required": False,
    "entry_card_name": None,
    "eta_required": False,
    "insurance_required": False,
    "vaccination_required": False,
    "onward_ticket_required": True,
    "summary": "Visa-free, 30 days, onward ticket required.",
    "advisory": "Border rules change without notice -- verify before you fly.",
}

_uid = iter(range(9000, 9_000_000))


def _pending_immigration_proposal(
    session: Session, trip_id: int, kind: str = "entry_card"
) -> Extraction:
    """A confirmation email that matched this trip and is waiting in Review --
    the `received` state's precondition. Deliberately built the way Phase 4
    writes one, so trip_readiness reads it the same way."""
    uid = next(_uid)
    email = EmailMessage(
        imap_uid=uid,
        message_id=f"<{uid}@mail.example>",
        from_addr="no-reply@imigrasi.go.id",
        subject="Your Indonesia e-CD is confirmed",
        snippet="Your electronic customs declaration has been approved.",
        received_at=datetime(2026, 9, 11, 9, 0),
        looks_like_immigration=True,
    )
    session.add(email)
    session.commit()
    session.refresh(email)
    ext = Extraction(
        email_message_id=email.id,
        kind=ExtractionKind.immigration,
        status=ExtractionStatus.pending,
        model="",
        payload_json=json.dumps({"requirement_kind": kind}),
        suggested_trip_id=trip_id,
    )
    session.add(ext)
    session.commit()
    session.refresh(ext)
    return ext


def _onward_journey(session: Session, country_code: str, depart_on: date) -> Leg:
    """A booked journey arriving `country_code` on `depart_on` -- i.e. the
    inbound leg of some later trip, which is exactly how an onward journey out
    of an earlier trip's country is recorded (return travel isn't tracked)."""
    trip = Trip()
    session.add(trip)
    session.commit()
    session.refresh(trip)
    leg = Leg(
        trip_id=trip.id,
        country_code=country_code,
        carrier="SQ",
        number="123",
        to_place="Singapore",
        depart_at=datetime.combine(depart_on, datetime.min.time()),
    )
    session.add(leg)
    session.commit()
    return leg


def test_arrival_card_reads_none_then_received_then_confirmed(session: Session, client):
    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")
    trip = session.get(Trip, trip_id)
    sync_requirements(session, trip, FakePolicyModel({("ID", "US"): VOA_INDONESIA}))

    # none: nothing has confirmed the arrival card.
    assert trip_readiness(session, trip)["arrival_card"]["state"] == "none"

    # received: a confirmation email matched and is pending in Review. This must
    # not write a Requirement -- the accept boundary holds.
    before = len(session.exec(select(Requirement).where(Requirement.trip_id == trip_id)).all())
    _pending_immigration_proposal(session, trip_id)
    reading = trip_readiness(session, trip)["arrival_card"]
    assert reading["state"] == "received"
    after = session.exec(
        select(Requirement).where(Requirement.trip_id == trip_id)
    ).all()
    assert len(after) == before
    entry_card = next(r for r in after if r.kind == RequirementKind.entry_card)
    assert entry_card.status == RequirementStatus.todo

    # confirmed: the entry_card requirement was accepted (approved).
    entry_card.status = RequirementStatus.approved
    entry_card.reference = "e-CD 4471"
    session.add(entry_card)
    session.commit()
    reading = trip_readiness(session, trip)["arrival_card"]
    assert reading["state"] == "confirmed"
    assert reading["reference"] == "e-CD 4471"


def test_a_pending_proposal_for_another_kind_does_not_mark_the_card_received(
    session: Session, client
):
    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")
    trip = session.get(Trip, trip_id)
    sync_requirements(session, trip, FakePolicyModel({("ID", "US"): VOA_INDONESIA}))

    # A Phase 5 proposal that read a *visa*, not an arrival card, is not an
    # arrival-card confirmation.
    _pending_immigration_proposal(session, trip_id, kind="visa")
    assert trip_readiness(session, trip)["arrival_card"]["state"] == "none"


def test_onward_ticket_is_none_when_the_policy_does_not_require_it(
    session: Session, client
):
    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="id")
    trip = session.get(Trip, trip_id)
    sync_requirements(session, trip, FakePolicyModel({("ID", "US"): VOA_INDONESIA}))
    assert trip_readiness(session, trip)["onward_ticket"] is None


def test_onward_ticket_confirmed_by_a_booked_onward_journey(session: Session, client):
    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="th")
    trip = session.get(Trip, trip_id)
    sync_requirements(session, trip, FakePolicyModel({("TH", "US"): ONWARD_REQUIRED_THAILAND}))

    # Required but nothing booked yet -> not confirmed, trip reads action.
    reading = trip_readiness(session, trip)
    assert reading["onward_ticket"] == {"required": True, "confirmed": False, "journey": None}
    assert reading["state"] == "action"

    # A journey leaving Thailand for Singapore the day the stay ends confirms it.
    _onward_journey(session, "SG", trip.end_date)
    reading = trip_readiness(session, trip)
    assert reading["onward_ticket"]["confirmed"] is True
    assert reading["onward_ticket"]["journey"]["to_place"] == "Singapore"
    # Onward is the only required item, and it's now settled.
    assert reading["state"] == "ready"


def test_onward_ticket_ignores_a_same_country_or_out_of_window_journey(
    session: Session, client
):
    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id, country_code="th")
    trip = session.get(Trip, trip_id)
    sync_requirements(session, trip, FakePolicyModel({("TH", "US"): ONWARD_REQUIRED_THAILAND}))

    # A leg arriving *Thailand* (the same country) is an inbound leg, not proof
    # of leaving; a leg to another country but weeks away doesn't align with the
    # end of this trip.
    _onward_journey(session, "TH", trip.end_date)
    _onward_journey(session, "SG", trip.end_date + timedelta(days=20))
    assert trip_readiness(session, trip)["onward_ticket"]["confirmed"] is False
