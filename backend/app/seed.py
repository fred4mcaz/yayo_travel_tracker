"""Sample data for development.

    python -m app.seed [--reset]

Deliberately covers every state the UI has to render: a complete past trip, an
ongoing one, and a future one with real holes in it (no hotel, no return leg, an
outstanding entry card) so gap detection has something to find in stage 7.
"""

import sys
from datetime import date, datetime, timedelta

from sqlmodel import Session, delete, select

from app.db import engine
from app.models import (
    CountryEntry,
    Leg,
    LegDirection,
    Nationality,
    Note,
    NoteCategory,
    Passport,
    PermitType,
    Requirement,
    RequirementKind,
    RequirementStatus,
    Stay,
    TravelMode,
    Trip,
)
from app.services.trips import refresh_trip_dates, sync_country_entries

TODAY = date.today()


def _reset(session: Session) -> None:
    for model in (CountryEntry, Note, Requirement, Leg, Stay, Trip, Passport):
        session.exec(delete(model))
    session.commit()


def seed(reset: bool = False) -> None:
    with Session(engine) as session:
        if reset:
            _reset(session)
        elif session.exec(select(Trip)).first():
            print("Database already has trips; pass --reset to replace them.")
            return

        # --- passports -----------------------------------------------------
        mx = Passport(
            nationality=Nationality.MX,
            nickname="Mexican",
            number_last4="4471",
            expires_on=TODAY + timedelta(days=900),
        )
        us = Passport(
            nationality=Nationality.US,
            nickname="US",
            number_last4="9032",
            expires_on=TODAY + timedelta(days=1600),
            is_default=True,
        )
        session.add_all([mx, us])
        session.commit()
        session.refresh(mx)
        session.refresh(us)

        # --- past trip: complete, nothing missing --------------------------
        past = Trip(title="Japan · spring", notes="Cherry blossom trip.")
        session.add(past)
        session.commit()
        session.refresh(past)

        start = TODAY - timedelta(days=130)
        session.add_all(
            [
                Stay(
                    trip_id=past.id, country_code="JP", city="Osaka",
                    lat=34.6937, lon=135.5023,
                    hotel_name="Hotel Granvia Osaka", check_in=start,
                    check_out=start + timedelta(days=4),
                    confirmation_code="GRV-771204", booking_source="Booking.com",
                    cost=612.40, currency="USD",
                ),
                Stay(
                    trip_id=past.id, country_code="JP", city="Kyoto",
                    lat=35.0116, lon=135.7681,
                    hotel_name="Hotel Kanra Kyoto",
                    check_in=start + timedelta(days=4),
                    check_out=start + timedelta(days=8),
                    confirmation_code="KNR-33918", booking_source="Direct",
                    cost=880.00, currency="USD",
                ),
                Leg(
                    trip_id=past.id, mode=TravelMode.flight,
                    direction=LegDirection.inbound, carrier="ANA", number="NH175",
                    from_place="Los Angeles", from_iata="LAX",
                    depart_at=datetime.combine(start - timedelta(days=1), datetime.min.time()).replace(hour=11, minute=40),
                    to_place="Osaka", to_iata="KIX",
                    arrive_at=datetime.combine(start, datetime.min.time()).replace(hour=15, minute=25),
                    confirmation_code="X4RT9P", seat="34K",
                ),
                Leg(
                    trip_id=past.id, mode=TravelMode.train,
                    direction=LegDirection.internal, carrier="JR", number="Nozomi 21",
                    from_place="Osaka", to_place="Kyoto",
                    depart_at=datetime.combine(start + timedelta(days=4), datetime.min.time()).replace(hour=10, minute=12),
                    arrive_at=datetime.combine(start + timedelta(days=4), datetime.min.time()).replace(hour=10, minute=28),
                ),
                Leg(
                    trip_id=past.id, mode=TravelMode.flight,
                    direction=LegDirection.outbound, carrier="ANA", number="NH176",
                    from_place="Osaka", from_iata="KIX",
                    depart_at=datetime.combine(start + timedelta(days=8), datetime.min.time()).replace(hour=17, minute=5),
                    to_place="Los Angeles", to_iata="LAX",
                    arrive_at=datetime.combine(start + timedelta(days=8), datetime.min.time()).replace(hour=10, minute=30),
                    confirmation_code="X4RT9P", seat="30A",
                ),
                Note(
                    trip_id=past.id, on_date=start + timedelta(days=5),
                    title="Dentist appointment in Kyoto",
                    body="Dr. Tanaka, 14:00. Bring the X-rays from March.",
                    category=NoteCategory.appointment, city="Kyoto",
                ),
            ]
        )
        session.commit()
        refresh_trip_dates(session, past)
        session.commit()
        sync_country_entries(session, past)
        jp_entry = session.exec(
            select(CountryEntry).where(CountryEntry.trip_id == past.id)
        ).one()
        jp_entry.passport_id = us.id
        jp_entry.permit_type = PermitType.visa_free
        jp_entry.permitted_days = 90
        jp_entry.port_of_entry = "Kansai (KIX)"
        session.add(jp_entry)
        session.commit()

        # --- ongoing trip --------------------------------------------------
        ongoing = Trip(title="Thailand · Bangkok")
        session.add(ongoing)
        session.commit()
        session.refresh(ongoing)

        session.add_all(
            [
                Stay(
                    trip_id=ongoing.id, country_code="TH", city="Bangkok",
                    lat=13.7563, lon=100.5018,
                    hotel_name="Riva Surya", check_in=TODAY - timedelta(days=2),
                    check_out=TODAY + timedelta(days=2),
                    confirmation_code="RS-889231", booking_source="Agoda",
                    cost=340.00, currency="USD",
                ),
                Leg(
                    trip_id=ongoing.id, mode=TravelMode.flight,
                    direction=LegDirection.inbound, carrier="Thai Airways",
                    number="TG315",
                    from_place="Los Angeles", from_iata="LAX",
                    depart_at=datetime.combine(TODAY - timedelta(days=3), datetime.min.time()).replace(hour=9, minute=50),
                    to_place="Bangkok", to_iata="BKK",
                    arrive_at=datetime.combine(TODAY - timedelta(days=2), datetime.min.time()).replace(hour=13, minute=5),
                    confirmation_code="QW82LM",
                ),
                Leg(
                    trip_id=ongoing.id, mode=TravelMode.flight,
                    direction=LegDirection.outbound, carrier="Thai Airways",
                    number="TG316",
                    from_place="Bangkok", from_iata="BKK",
                    depart_at=datetime.combine(TODAY + timedelta(days=2), datetime.min.time()).replace(hour=22, minute=15),
                    to_place="Los Angeles", to_iata="LAX",
                    arrive_at=datetime.combine(TODAY + timedelta(days=2), datetime.min.time()).replace(hour=23, minute=40),
                    confirmation_code="QW82LM",
                ),
            ]
        )
        session.commit()
        refresh_trip_dates(session, ongoing)
        session.commit()
        sync_country_entries(session, ongoing)
        th_entry = session.exec(
            select(CountryEntry).where(CountryEntry.trip_id == ongoing.id)
        ).one()
        th_entry.passport_id = us.id
        th_entry.permit_type = PermitType.visa_free
        th_entry.permitted_days = 60
        th_entry.must_exit_by = TODAY + timedelta(days=58)
        session.add(th_entry)
        session.commit()

        # --- future trip: deliberately incomplete --------------------------
        # No hotel confirmation, no return leg, entry card outstanding, and no
        # passport chosen. This is the trip gap detection should light up.
        future = Trip(title="Vietnam · Hanoi")
        session.add(future)
        session.commit()
        session.refresh(future)

        depart = TODAY + timedelta(days=48)
        session.add_all(
            [
                Stay(
                    trip_id=future.id, country_code="VN", city="Hanoi",
                    lat=21.0278, lon=105.8342,
                    hotel_name="", check_in=depart,
                    check_out=depart + timedelta(days=6),
                ),
                Leg(
                    trip_id=future.id, mode=TravelMode.flight,
                    direction=LegDirection.inbound, carrier="Vietnam Airlines",
                    number="VN610",
                    from_place="Bangkok", from_iata="BKK",
                    depart_at=datetime.combine(depart, datetime.min.time()).replace(hour=8, minute=20),
                    to_place="Hanoi", to_iata="HAN",
                    arrive_at=datetime.combine(depart, datetime.min.time()).replace(hour=10, minute=5),
                    confirmation_code="VN77KD",
                ),
                Requirement(
                    trip_id=future.id, kind=RequirementKind.entry_card,
                    label="Vietnam e-entry card", country_code="VN",
                    status=RequirementStatus.todo,
                    due_date=depart - timedelta(days=3),
                ),
                Note(
                    trip_id=future.id, on_date=depart + timedelta(days=1),
                    title="Pay the Hanoi apartment deposit",
                    category=NoteCategory.reminder, city="Hanoi",
                ),
            ]
        )
        session.commit()
        refresh_trip_dates(session, future)
        session.commit()
        sync_country_entries(session, future)

        # A standalone note, unattached to any trip.
        session.add(
            Note(
                on_date=TODAY + timedelta(days=20),
                title="Renew Mexican passport",
                body="Consulate appointments book out ~6 weeks.",
                category=NoteCategory.reminder,
            )
        )
        session.commit()

        print(f"Seeded 3 trips, 2 passports, 3 notes. Today is {TODAY}.")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
