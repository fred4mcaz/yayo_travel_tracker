"""Phase 4: matching, and the line that accepting is the only writer.

The invariant this whole stage is built around:

    Nothing writes to trip data without an explicit accept.

So the load-bearing tests here count Trip/Stay/Leg rows around matching and
rejecting (they must not move) and around accepting (only then). And the
one-country rule: a booking for a different country than an overlapping trip
must propose a *new* trip, not fail.
"""

import json
from datetime import date, datetime

from sqlmodel import Session, select

from app.models import (
    CountryEntry,
    EmailMessage,
    Extraction,
    ExtractionStatus,
    Leg,
    Stay,
    Trip,
)
from app.services.extraction import validate_booking
from app.services.review import (
    NotAcceptable,
    accept_extraction,
    find_matching_trip,
    reject_extraction,
    suggest,
)
from app.services.trips import refresh_trip_dates, sync_country_entries


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _make_trip(session: Session, country: str, city: str, check_in: str, check_out: str) -> Trip:
    """A trip with one hotel, with its derived state brought up to date -- the
    same shape the API leaves a trip in."""
    trip = Trip(notes="")
    session.add(trip)
    session.commit()
    session.refresh(trip)
    session.add(
        Stay(
            trip_id=trip.id,
            country_code=country,
            city=city,
            check_in=date.fromisoformat(check_in),
            check_out=date.fromisoformat(check_out),
        )
    )
    session.commit()
    refresh_trip_dates(session, trip)
    session.commit()
    sync_country_entries(session, trip)
    session.refresh(trip)
    return trip


def _booking(**kw):
    payload = {
        "kind": kw.get("kind", "hotel"),
        "country_code": kw.get("country_code", "VN"),
        "city": kw.get("city", "Hanoi"),
        "start_date": kw.get("start_date", "2026-08-30"),
        "end_date": kw.get("end_date", "2026-09-03"),
        "hotel_name": kw.get("hotel_name", "Sofitel Legend"),
        "carrier": kw.get("carrier"),
        "confirmation_code": kw.get("confirmation_code", "4471"),
    }
    return validate_booking(payload)


_next_uid = iter(range(100, 100_000))


def _extraction(session: Session, *, suggested_trip_id=None, **kw) -> Extraction:
    # Extraction.email_message_id is a real foreign key, so it needs a row to
    # point at. The email's content is irrelevant here -- phase 3 owns that.
    uid = next(_next_uid)
    email = EmailMessage(imap_uid=uid, message_id=f"<{uid}@mail.example>", looks_like_travel=True)
    session.add(email)
    session.commit()
    session.refresh(email)

    booking = _booking(**kw)
    row = Extraction(
        email_message_id=email.id,
        model="claude-sonnet-5",
        payload_json=json.dumps(booking.payload(), sort_keys=True),
        confidence=0.9,
        status=ExtractionStatus.pending,
        suggested_trip_id=suggested_trip_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _counts(session: Session) -> tuple[int, int, int]:
    return (
        len(session.exec(select(Trip)).all()),
        len(session.exec(select(Stay)).all()),
        len(session.exec(select(Leg)).all()),
    )


# --------------------------------------------------------------------------
# Matching -- read only
# --------------------------------------------------------------------------


def test_same_country_overlapping_dates_matches(session: Session):
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    booking = _booking(country_code="VN", start_date="2026-08-31", end_date="2026-09-02")

    assert find_matching_trip(session, booking) == trip.id


def test_different_country_overlapping_dates_does_not_match(session: Session):
    """The one-country rule at the match stage: a Thailand booking over a
    Vietnam trip is a *new trip*, not a match to be rejected later."""
    _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    booking = _booking(country_code="TH", city="Bangkok", start_date="2026-09-01")

    assert find_matching_trip(session, booking) is None


def test_dates_within_slack_match(session: Session):
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    # Arrives two days before the first checkout window.
    booking = _booking(country_code="VN", start_date="2026-08-28", end_date="2026-08-29")

    assert find_matching_trip(session, booking) == trip.id


def test_dates_just_outside_slack_do_not_match(session: Session):
    _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    # Three days before the trip starts -- beyond the two-day slack.
    booking = _booking(country_code="VN", start_date="2026-08-25", end_date="2026-08-26")

    assert find_matching_trip(session, booking) is None


def test_a_dateless_booking_never_matches(session: Session):
    _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    booking = _booking(country_code="VN", start_date=None, end_date=None)

    assert find_matching_trip(session, booking) is None


def test_a_countryless_leg_overlapping_two_trips_is_ambiguous(session: Session):
    _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    _make_trip(session, "TH", "Bangkok", "2026-09-01", "2026-09-05")
    booking = _booking(
        kind="flight", country_code=None, city=None, start_date="2026-09-02", end_date=None
    )

    assert find_matching_trip(session, booking) is None


def test_a_countryless_leg_overlapping_one_trip_matches(session: Session):
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    booking = _booking(
        kind="flight", country_code=None, city=None, start_date="2026-08-31", end_date=None
    )

    assert find_matching_trip(session, booking) == trip.id


def test_suggest_records_the_match_without_touching_trip_data(session: Session):
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    before = _counts(session)
    ext = _extraction(session, country_code="VN", start_date="2026-08-31")

    suggested = suggest(session, ext)
    session.commit()

    assert suggested == trip.id
    assert ext.suggested_trip_id == trip.id
    assert _counts(session) == before  # nothing created


# --------------------------------------------------------------------------
# The accept boundary
# --------------------------------------------------------------------------


def test_matching_and_rejecting_never_write_trip_data(session: Session):
    """The invariant, stated as a test: only accept moves these counts."""
    _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    before = _counts(session)

    ext = _extraction(session, country_code="TH", city="Bangkok", start_date="2026-09-10")
    suggest(session, ext)
    session.commit()
    assert _counts(session) == before

    reject_extraction(session, ext)
    assert _counts(session) == before
    assert ext.status == ExtractionStatus.rejected


def test_accept_with_a_suggestion_attaches_to_the_existing_trip(session: Session):
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    ext = _extraction(
        session,
        suggested_trip_id=trip.id,
        country_code="VN",
        city="Hue",
        start_date="2026-09-01",
        end_date="2026-09-02",
    )

    result = accept_extraction(session, ext)

    assert result.trip_id == trip.id
    assert result.created_new_trip is False
    trips, stays, legs = _counts(session)
    assert trips == 1  # no new trip
    assert stays == 2  # the new hotel joined the existing trip
    assert ext.status == ExtractionStatus.accepted
    assert ext.reviewed_at is not None


def test_accept_without_a_suggestion_creates_a_new_trip(session: Session):
    ext = _extraction(session, country_code="JP", city="Osaka", start_date="2026-10-01",
                      end_date="2026-10-05")
    before = _counts(session)

    result = accept_extraction(session, ext)

    assert result.created_new_trip is True
    trips, stays, _ = _counts(session)
    assert trips == before[0] + 1
    assert stays == before[1] + 1


def test_different_country_booking_accepts_as_a_new_trip_without_error(
    session: Session,
):
    """End to end of the one-country rule: a Thailand proposal against a Vietnam
    trip proposes nothing, then accepts cleanly as its own trip -- no 409, no
    NotAcceptable."""
    vietnam = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    ext = _extraction(session, country_code="TH", city="Bangkok",
                      start_date="2026-09-01", end_date="2026-09-04")

    suggest(session, ext)
    session.commit()
    assert ext.suggested_trip_id is None  # not the Vietnam trip

    result = accept_extraction(session, ext)

    assert result.created_new_trip is True
    assert result.trip_id != vietnam.id
    # Two separate trips, each in its own country.
    trips = session.exec(select(Trip)).all()
    assert len(trips) == 2


def test_accept_syncs_the_country_entry(session: Session):
    ext = _extraction(session, country_code="JP", city="Tokyo", start_date="2026-10-01",
                      end_date="2026-10-05")

    result = accept_extraction(session, ext)

    entry = session.exec(
        select(CountryEntry).where(CountryEntry.trip_id == result.trip_id)
    ).first()
    assert entry is not None
    assert entry.country_code == "JP"


def test_accept_refreshes_the_trip_span(session: Session):
    ext = _extraction(session, country_code="JP", city="Tokyo", start_date="2026-10-01",
                      end_date="2026-10-05")

    result = accept_extraction(session, ext)

    trip = session.get(Trip, result.trip_id)
    assert trip.start_date == date(2026, 10, 1)
    assert trip.end_date == date(2026, 10, 5)


def test_accept_a_leg_creates_a_leg_not_a_stay(session: Session):
    ext = _extraction(
        session,
        kind="flight",
        country_code="VN",
        city=None,
        carrier="Vietnam Airlines",
        start_date="2026-08-30",
        end_date=None,
        hotel_name=None,
    )

    result = accept_extraction(session, ext)

    assert result.leg_id is not None
    assert result.stay_id is None
    leg = session.get(Leg, result.leg_id)
    assert leg.carrier == "Vietnam Airlines"
    assert leg.depart_at == datetime(2026, 8, 30, 0, 0)


def test_accept_records_what_it_applied(session: Session):
    ext = _extraction(session, country_code="JP", city="Kyoto", start_date="2026-10-01",
                      end_date="2026-10-03")

    result = accept_extraction(session, ext)

    applied = json.loads(ext.applied_ids_json)
    assert applied["trip_id"] == result.trip_id
    assert applied["stay_id"] == result.stay_id
    assert applied["created_new_trip"] is True


def test_accepting_an_already_reviewed_extraction_is_refused(session: Session):
    ext = _extraction(session, country_code="JP", city="Nara", start_date="2026-10-01",
                      end_date="2026-10-02")
    accept_extraction(session, ext)

    try:
        accept_extraction(session, ext)
        assert False, "expected NotAcceptable"
    except NotAcceptable:
        pass


def test_a_failed_accept_leaves_no_orphan_trip(session: Session):
    """The invariant holds even when accept *fails*: an incomplete hotel is
    refused before any trip is created, not after."""
    before = _counts(session)
    ext = _extraction(session, country_code="TH", city=None, start_date="2026-09-01",
                      end_date="2026-09-04")

    try:
        accept_extraction(session, ext)
        assert False, "expected NotAcceptable"
    except NotAcceptable:
        pass

    assert _counts(session) == before  # no orphan Trip, no Stay
    assert ext.status == ExtractionStatus.pending  # still reviewable


def test_stale_suggestion_to_a_wrong_country_trip_is_refused(session: Session):
    """Defence in depth: if a trip's country changed after the suggestion was
    recorded, accept must not split it across two countries."""
    trip = _make_trip(session, "VN", "Hanoi", "2026-08-30", "2026-09-03")
    ext = _extraction(session, suggested_trip_id=trip.id, country_code="TH",
                      city="Bangkok", start_date="2026-08-31", end_date="2026-09-01")

    try:
        accept_extraction(session, ext)
        assert False, "expected NotAcceptable"
    except NotAcceptable:
        pass
    # The Vietnam trip was not touched.
    assert len(session.exec(select(Stay)).all()) == 1
