"""Phase 0: the new schema for immigration readiness.

Just the plumbing -- the EntryPolicy cache table round-trips, and the two new
columns (Requirement.source, EmailMessage.looks_like_immigration) carry the
defaults the migration backfills onto existing rows. The policy service,
requirement materialisation, and the classifier itself are later phases.
"""

from datetime import datetime

from sqlmodel import select

from app.models import (
    Actor,
    EmailMessage,
    EntryPolicy,
    Nationality,
    PermitType,
    Requirement,
    RequirementKind,
    Trip,
)


def test_entry_policy_round_trips(session):
    policy = EntryPolicy(
        country_code="ID",
        nationality=Nationality.US,
        permit_type=PermitType.visa_on_arrival,
        permitted_days=30,
        visa_required=True,
        entry_card_required=True,
        entry_card_name="Indonesia e-CD (electronic customs/arrival card)",
        summary="Visa-on-arrival for US passport holders, 30 days.",
        advisory="Border rules change without notice -- verify before you fly.",
        source_model="anthropic/claude-sonnet-5",
    )
    session.add(policy)
    session.commit()
    session.refresh(policy)

    fetched = session.exec(
        select(EntryPolicy)
        .where(EntryPolicy.country_code == "ID")
        .where(EntryPolicy.nationality == Nationality.US)
    ).one()
    assert fetched.id == policy.id
    assert fetched.permit_type == PermitType.visa_on_arrival
    assert fetched.entry_card_required is True
    # Asked-about-but-not-required kinds default false, not a stored "n/a".
    assert fetched.insurance_required is False
    assert fetched.vaccination_required is False
    assert fetched.onward_ticket_required is False
    assert fetched.fetched_at is not None


def test_entry_policy_unique_per_country_and_nationality(session):
    session.add(EntryPolicy(country_code="JP", nationality=Nationality.US))
    session.commit()
    session.add(EntryPolicy(country_code="JP", nationality=Nationality.MX))
    session.commit()  # different nationality, same country -- fine

    dupe = EntryPolicy(country_code="JP", nationality=Nationality.US)
    session.add(dupe)
    try:
        session.commit()
        assert False, "expected the unique constraint to reject a repeat pair"
    except Exception:
        session.rollback()


def test_requirement_source_defaults_manual(session):
    trip = Trip()
    session.add(trip)
    session.commit()
    session.refresh(trip)

    req = Requirement(trip_id=trip.id, kind=RequirementKind.visa)
    session.add(req)
    session.commit()
    session.refresh(req)

    assert req.source == Actor.manual


def test_requirement_source_system_for_materialised_rows(session):
    trip = Trip()
    session.add(trip)
    session.commit()
    session.refresh(trip)

    req = Requirement(
        trip_id=trip.id, kind=RequirementKind.entry_card, source=Actor.system
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    assert req.source == Actor.system


def test_email_message_looks_like_immigration_defaults_false(session):
    email = EmailMessage(
        imap_uid=1,
        message_id="<abc@example.com>",
        from_addr="noreply@imigrasi.go.id",
        subject="Your e-VOA has been approved",
        received_at=datetime.now(),
    )
    session.add(email)
    session.commit()
    session.refresh(email)

    assert email.looks_like_immigration is False
    assert email.looks_like_travel is False  # the two flags are independent
