from datetime import date, timedelta

TODAY = date.today()


def _mk_trip(client, title="Test trip") -> int:
    r = client.post("/api/trips", json={"title": title})
    assert r.status_code == 201
    return r.json()["id"]


def _mk_stay(client, trip_id, **overrides) -> dict:
    payload = {
        "country_code": "vn",
        "city": "Hanoi",
        "check_in": str(TODAY + timedelta(days=10)),
        "check_out": str(TODAY + timedelta(days=15)),
        "hotel_name": "Sofitel Legend",
        "confirmation_code": "4417-88213",
    }
    payload.update(overrides)
    r = client.post(f"/api/trips/{trip_id}/stays", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_trip_span_derives_from_stays(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id)
    assert detail["start_date"] == str(TODAY + timedelta(days=10))
    assert detail["end_date"] == str(TODAY + timedelta(days=15))
    assert detail["nights"] == 5


def test_leg_extends_trip_span_before_first_checkin(client):
    """A red-eye departing the night before still belongs to the trip."""
    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id)
    r = client.post(
        f"/api/trips/{trip_id}/legs",
        json={
            "mode": "flight",
            "direction": "inbound",
            "depart_at": f"{TODAY + timedelta(days=9)}T22:30:00",
            "arrive_at": f"{TODAY + timedelta(days=10)}T06:15:00",
            "from_iata": "bkk",
            "to_iata": "han",
        },
    )
    assert r.status_code == 201
    detail = r.json()
    assert detail["start_date"] == str(TODAY + timedelta(days=9))
    assert detail["legs"][0]["from_iata"] == "BKK"  # normalised to upper


def test_country_code_normalised_and_entry_autocreated(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id, country_code="vn")
    assert detail["stays"][0]["country_code"] == "VN"
    # Crossing into a new country creates the entry row the passport picker needs.
    assert len(detail["entries"]) == 1
    assert detail["entries"][0]["country_code"] == "VN"
    assert detail["entries"][0]["entered_on"] == str(TODAY + timedelta(days=10))
    assert detail["entries"][0]["passport_id"] is None


def test_entry_removed_when_country_no_longer_visited(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id, country_code="VN")
    stay_id = detail["stays"][0]["id"]
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}", json={"country_code": "th"}
    ).json()
    codes = [e["country_code"] for e in detail["entries"]]
    assert codes == ["TH"]


def test_existing_entry_not_clobbered_by_unrelated_edit(client):
    """Recording the passport used must survive editing a hotel name."""
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id)
    p = client.post(
        "/api/passports", json={"nationality": "US", "number_last4": "9032"}
    ).json()
    entry_id = detail["entries"][0]["id"]
    client.patch(
        f"/api/trips/{trip_id}/entries/{entry_id}",
        json={"passport_id": p["id"], "port_of_entry": "Noi Bai"},
    )

    stay_id = detail["stays"][0]["id"]
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}", json={"hotel_name": "Other hotel"}
    ).json()
    assert detail["entries"][0]["passport_id"] == p["id"]
    assert detail["entries"][0]["port_of_entry"] == "Noi Bai"


def test_trip_status(client):
    past = _mk_trip(client, "past")
    _mk_stay(
        client, past,
        check_in=str(TODAY - timedelta(days=20)),
        check_out=str(TODAY - timedelta(days=10)),
    )
    ongoing = _mk_trip(client, "ongoing")
    _mk_stay(
        client, ongoing,
        check_in=str(TODAY - timedelta(days=2)),
        check_out=str(TODAY + timedelta(days=2)),
    )
    future = _mk_trip(client, "future")
    _mk_stay(client, future)
    _mk_trip(client, "undated")  # no segments, so no derivable span

    by_title = {t["title"]: t["status"] for t in client.get("/api/trips").json()}
    assert by_title == {
        "past": "past",
        "ongoing": "ongoing",
        "future": "future",
        "undated": "undated",
    }


def test_patch_omitted_field_is_left_alone_but_null_clears(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id)
    stay_id = detail["stays"][0]["id"]

    # Omitting hotel_name must not wipe it.
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}", json={"city": "Da Nang"}
    ).json()
    assert detail["stays"][0]["hotel_name"] == "Sofitel Legend"
    assert detail["stays"][0]["city"] == "Da Nang"

    # An explicit null clears the field. Because `notes` is a non-nullable text
    # column it resets to its declared default rather than raising IntegrityError.
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}",
        json={"hotel_name": None, "notes": None},
    ).json()
    assert detail["stays"][0]["hotel_name"] == ""
    assert detail["stays"][0]["notes"] == ""

    # A genuinely nullable column does accept null.
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}", json={"cost": 120.0}
    ).json()
    assert detail["stays"][0]["cost"] == 120.0
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}", json={"cost": None}
    ).json()
    assert detail["stays"][0]["cost"] is None


def test_checkout_before_checkin_rejected(client):
    trip_id = _mk_trip(client)
    r = client.post(
        f"/api/trips/{trip_id}/stays",
        json={
            "country_code": "VN",
            "city": "Hanoi",
            "check_in": str(TODAY + timedelta(days=10)),
            "check_out": str(TODAY + timedelta(days=3)),
        },
    )
    assert r.status_code == 422


def test_segment_of_another_trip_is_404_not_403(client):
    a = _mk_trip(client, "a")
    b = _mk_trip(client, "b")
    stay_id = _mk_stay(client, a)["stays"][0]["id"]
    r = client.patch(f"/api/trips/{b}/stays/{stay_id}", json={"city": "Nope"})
    assert r.status_code == 404


def test_deleting_trip_cascades_to_segments(client, session):
    from sqlmodel import select

    from app.models import CountryEntry, Stay

    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id)
    assert client.delete(f"/api/trips/{trip_id}").status_code == 204

    session.expire_all()
    assert session.exec(select(Stay)).all() == []
    assert session.exec(select(CountryEntry)).all() == []


def test_trip_memo_survives_alongside_note_records(client):
    """Regression: trip_detail spread model_dump() (with a `notes` string) and
    then wrote `notes` again as the Note array, silently dropping the memo."""
    trip_id = _mk_trip(client)
    client.patch(f"/api/trips/{trip_id}", json={"notes": "Cherry blossom trip."})
    client.post(
        "/api/notes",
        json={
            "trip_id": trip_id,
            "on_date": str(TODAY + timedelta(days=11)),
            "title": "Dentist in Hanoi",
        },
    )

    detail = client.get(f"/api/trips/{trip_id}").json()
    assert detail["notes"] == "Cherry blossom trip."  # the memo, a string
    assert [n["title"] for n in detail["notes_list"]] == ["Dentist in Hanoi"]


def test_is_confirmed_requires_name_and_code(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id, hotel_name="Sofitel", confirmation_code="")
    stay = detail["stays"][0]
    # A hotel with no reference number is an intention, not a booking.
    assert stay["hotel_name"] and not stay["confirmation_code"]


# --- derived labels -------------------------------------------------------


def test_trip_needs_no_name(client):
    """Creating a trip must not require inventing a name for it."""
    r = client.post("/api/trips", json={})
    assert r.status_code == 201
    assert r.json()["label"] == "New trip"


def test_label_is_city_and_hotel_for_one_stop(client):
    trip_id = client.post("/api/trips", json={}).json()["id"]
    detail = _mk_stay(client, trip_id, city="Hanoi", hotel_name="Sofitel Legend")
    assert detail["label"] == "Hanoi · Sofitel Legend"


def test_label_is_just_the_city_when_no_hotel_yet(client):
    trip_id = client.post("/api/trips", json={}).json()["id"]
    detail = _mk_stay(client, trip_id, city="Hanoi", hotel_name="")
    assert detail["label"] == "Hanoi"


def test_label_becomes_a_route_for_several_stops(client):
    """One international trip, several hotels — the point of this change."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _mk_stay(client, trip_id, city="Hanoi", hotel_name="Sofitel")
    _mk_stay(
        client, trip_id, city="Hue",
        check_in=str(TODAY + timedelta(days=15)),
        check_out=str(TODAY + timedelta(days=18)),
    )
    detail = _mk_stay(
        client, trip_id, city="Hoi An",
        check_in=str(TODAY + timedelta(days=18)),
        check_out=str(TODAY + timedelta(days=22)),
    )
    assert detail["label"] == "Hanoi → Hue → Hoi An"
    # Still one trip, one country entry, one journey.
    assert len(detail["stays"]) == 3
    assert len(detail["entries"]) == 1


def test_label_truncates_a_long_route(client):
    trip_id = client.post("/api/trips", json={}).json()["id"]
    for i, city in enumerate(["Hanoi", "Hue", "Hoi An", "Da Nang", "Da Lat"]):
        _mk_stay(
            client, trip_id, city=city,
            check_in=str(TODAY + timedelta(days=10 + i * 3)),
            check_out=str(TODAY + timedelta(days=12 + i * 3)),
        )
    detail = client.get(f"/api/trips/{trip_id}").json()
    assert detail["label"] == "Hanoi → Hue +3 more"


def test_an_explicit_title_still_wins(client):
    """Imported or hand-named trips keep their name."""
    trip_id = client.post("/api/trips", json={"title": "Honeymoon"}).json()["id"]
    detail = _mk_stay(client, trip_id, city="Hanoi", hotel_name="Sofitel")
    assert detail["label"] == "Honeymoon"


def test_legs_default_to_the_inbound_journey(client):
    """The travel form no longer asks; every leg it creates is getting there."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    detail = client.post(
        f"/api/trips/{trip_id}/legs",
        json={"from_place": "Los Angeles", "to_place": "Hanoi"},
    ).json()
    assert detail["legs"][0]["direction"] == "inbound"


# --- country-first shape --------------------------------------------------


def _stay_in(client, trip_id, country, city, start_offset, nights=3, **extra):
    return _mk_stay(
        client, trip_id,
        country_code=country, city=city,
        check_in=str(TODAY + timedelta(days=start_offset)),
        check_out=str(TODAY + timedelta(days=start_offset + nights)),
        **extra,
    )


def test_country_to_country_to_country(client):
    """The shape this app is for: several countries, hotels within each."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, hotel_name="Sofitel")
    _stay_in(client, trip_id, "VN", "Hue", 13, hotel_name="Pilgrimage")
    _stay_in(client, trip_id, "TH", "Bangkok", 16, hotel_name="Riva Surya")
    detail = _stay_in(client, trip_id, "JP", "Osaka", 20, hotel_name="Granvia")

    segments = detail["country_segments"]
    assert [s["country_code"] for s in segments] == ["VN", "TH", "JP"]
    assert [s["country_name"] for s in segments] == ["Vietnam", "Thailand", "Japan"]

    vietnam = segments[0]
    assert [s["city"] for s in vietnam["stays"]] == ["Hanoi", "Hue"]
    assert vietnam["nights"] == 6

    # One entry per country, each with its own passport slot.
    assert all(s["entry"] is not None for s in segments)
    assert all(s["passport_id"] is None for s in segments)
    assert detail["label"] == "Vietnam → Thailand → Japan"


def test_each_country_takes_its_own_passport(client):
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10)
    detail = _stay_in(client, trip_id, "MX", "Guadalajara", 16)

    mx_pp = client.post("/api/passports", json={"nationality": "MX"}).json()
    us_pp = client.post("/api/passports", json={"nationality": "US"}).json()

    by_code = {s["country_code"]: s for s in detail["country_segments"]}
    client.patch(
        f"/api/trips/{trip_id}/entries/{by_code['VN']['entry']['id']}",
        json={"passport_id": us_pp["id"]},
    )
    detail = client.patch(
        f"/api/trips/{trip_id}/entries/{by_code['MX']['entry']['id']}",
        json={"passport_id": mx_pp["id"]},
    ).json()

    got = {s["country_code"]: s["passport_id"] for s in detail["country_segments"]}
    assert got == {"VN": us_pp["id"], "MX": mx_pp["id"]}


def test_a_leg_belongs_to_the_country_it_delivers_you_into(client):
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10)
    _stay_in(client, trip_id, "TH", "Bangkok", 16)

    client.post(
        f"/api/trips/{trip_id}/legs",
        json={"country_code": "TH", "from_place": "Hanoi", "to_place": "Bangkok"},
    )
    detail = client.post(
        f"/api/trips/{trip_id}/legs",
        json={"country_code": "VN", "from_place": "Los Angeles", "to_place": "Hanoi"},
    ).json()

    by_code = {s["country_code"]: s for s in detail["country_segments"]}
    assert [leg["to_place"] for leg in by_code["VN"]["legs"]] == ["Hanoi"]
    assert [leg["to_place"] for leg in by_code["TH"]["legs"]] == ["Bangkok"]


def test_a_country_with_only_a_flight_still_gets_a_section(client):
    """Booking the flight before the hotel is the normal order of events."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10)
    detail = client.post(
        f"/api/trips/{trip_id}/legs",
        json={"country_code": "TH", "from_place": "Hanoi", "to_place": "Bangkok"},
    ).json()

    codes = [s["country_code"] for s in detail["country_segments"]]
    assert "TH" in codes
    thailand = next(s for s in detail["country_segments"] if s["country_code"] == "TH")
    assert thailand["stays"] == []
    assert len(thailand["legs"]) == 1


def test_single_country_trip_is_still_named_by_city(client):
    """Naming a one-country trip after the country would say nothing new."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    detail = _stay_in(client, trip_id, "VN", "Hanoi", 10, hotel_name="Sofitel")
    assert detail["label"] == "Hanoi · Sofitel"


# --- unbooked nights ------------------------------------------------------


def _segments(client, trip_id):
    detail = client.get(f"/api/trips/{trip_id}").json()
    return {s["country_code"]: s for s in detail["country_segments"]}


def test_gap_between_two_hotels_in_one_country(client):
    """Vietnam 10th-20th with hotels covering 10th-13th and 16th-20th leaves
    three nights nobody booked."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=3)
    _stay_in(client, trip_id, "VN", "Hue", 16, nights=4)
    _stay_in(client, trip_id, "TH", "Bangkok", 20, nights=3)

    vn = _segments(client, trip_id)["VN"]
    assert vn["unbooked"] == [
        {
            "from": str(TODAY + timedelta(days=13)),
            "to": str(TODAY + timedelta(days=16)),
            "nights": 3,
        }
    ]


def test_no_gap_when_hotels_run_back_to_back(client):
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=3)
    _stay_in(client, trip_id, "VN", "Hue", 13, nights=3)
    _stay_in(client, trip_id, "TH", "Bangkok", 16, nights=2)

    assert _segments(client, trip_id)["VN"]["unbooked"] == []


def test_gap_before_the_next_country_starts(client):
    """Hotels stop on the 14th but you do not fly to Thailand until the 18th."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=4)
    _stay_in(client, trip_id, "TH", "Bangkok", 18, nights=3)

    vn = _segments(client, trip_id)["VN"]
    assert vn["unbooked"] == [
        {
            "from": str(TODAY + timedelta(days=14)),
            "to": str(TODAY + timedelta(days=18)),
            "nights": 4,
        }
    ]


def test_gap_between_landing_and_the_first_hotel(client):
    """Landing the day before the first booking is a night with nowhere to go."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=4)
    client.post(
        f"/api/trips/{trip_id}/legs",
        json={
            "country_code": "VN",
            "to_place": "Hanoi",
            "arrive_at": f"{TODAY + timedelta(days=9)}T21:30:00",
        },
    )
    vn = _segments(client, trip_id)["VN"]
    assert vn["unbooked"] == [
        {
            "from": str(TODAY + timedelta(days=9)),
            "to": str(TODAY + timedelta(days=10)),
            "nights": 1,
        }
    ]


def test_last_country_never_reports_a_trailing_gap(client):
    """Return travel is not tracked, so there is no date to measure against."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=3)
    _stay_in(client, trip_id, "JP", "Osaka", 20, nights=4)

    assert _segments(client, trip_id)["JP"]["unbooked"] == []


def test_country_with_a_flight_but_no_hotel_at_all(client):
    """A booked flight and no room is the loudest version of this problem."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=3)
    client.post(
        f"/api/trips/{trip_id}/legs",
        json={
            "country_code": "TH",
            "to_place": "Bangkok",
            "arrive_at": f"{TODAY + timedelta(days=13)}T10:00:00",
        },
    )
    _stay_in(client, trip_id, "JP", "Osaka", 18, nights=3)

    th = _segments(client, trip_id)["TH"]
    assert th["stays"] == []
    assert th["unbooked"] == [
        {
            "from": str(TODAY + timedelta(days=13)),
            "to": str(TODAY + timedelta(days=18)),
            "nights": 5,
        }
    ]


def test_overlapping_hotels_are_not_a_gap(client):
    """Double-booked is a different problem; it must not read as unbooked."""
    trip_id = client.post("/api/trips", json={}).json()["id"]
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=5)
    _stay_in(client, trip_id, "VN", "Hue", 12, nights=2)
    _stay_in(client, trip_id, "TH", "Bangkok", 15, nights=2)

    assert _segments(client, trip_id)["VN"]["unbooked"] == []
