"""Trip tests.

A trip is one international stay in one country: the journey that got you
there, the passport you entered on, and every hotel you sleep in while there.
"""

from datetime import date, timedelta

TODAY = date.today()


def _mk_trip(client) -> int:
    r = client.post("/api/trips", json={})
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


def _stay_in(client, trip_id, country, city, start_offset, nights=3, **extra):
    return _mk_stay(
        client, trip_id,
        country_code=country, city=city,
        check_in=str(TODAY + timedelta(days=start_offset)),
        check_out=str(TODAY + timedelta(days=start_offset + nights)),
        **extra,
    )


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_a_trip_needs_nothing_to_start(client):
    body = client.post("/api/trips", json={}).json()
    assert body["label"] == "New trip"
    assert body["country"] is None


def test_trip_span_derives_from_hotels(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id)
    assert detail["start_date"] == str(TODAY + timedelta(days=10))
    assert detail["end_date"] == str(TODAY + timedelta(days=15))
    assert detail["nights"] == 5


def test_leg_extends_the_span_before_the_first_checkin(client):
    """A red-eye departing the night before still belongs to the trip."""
    trip_id = _mk_trip(client)
    _mk_stay(client, trip_id)
    detail = client.post(
        f"/api/trips/{trip_id}/legs",
        json={
            "depart_at": f"{TODAY + timedelta(days=9)}T22:30:00",
            "arrive_at": f"{TODAY + timedelta(days=10)}T06:15:00",
            "from_iata": "bkk",
            "to_iata": "han",
        },
    ).json()
    assert detail["start_date"] == str(TODAY + timedelta(days=9))
    assert detail["country"]["legs"][0]["from_iata"] == "BKK"  # normalised
    # The journey inherits the trip's country without being told.
    assert detail["country"]["legs"][0]["country_code"] == "VN"


def test_country_and_entry_come_from_the_first_hotel(client):
    trip_id = _mk_trip(client)
    country = _mk_stay(client, trip_id, country_code="vn")["country"]
    assert country["country_code"] == "VN"
    assert country["country_name"] == "Vietnam"
    assert country["entered_on"] == str(TODAY + timedelta(days=10))
    assert country["passport_id"] is None


def test_several_hotels_in_one_country(client):
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10, hotel_name="Sofitel")
    _stay_in(client, trip_id, "VN", "Hue", 13, hotel_name="Pilgrimage")
    detail = _stay_in(client, trip_id, "VN", "Hoi An", 16, hotel_name="Anantara")

    assert [s["city"] for s in detail["country"]["stays"]] == [
        "Hanoi", "Hue", "Hoi An",
    ]
    assert detail["label"] == "Hanoi → Hue → Hoi An"
    assert detail["country"]["nights"] == 9


# --------------------------------------------------------------------------
# One country per trip
# --------------------------------------------------------------------------


def test_a_second_country_is_refused(client):
    """New trip is how a new country gets recorded."""
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10)
    r = client.post(
        f"/api/trips/{trip_id}/stays",
        json={
            "country_code": "TH",
            "city": "Bangkok",
            "check_in": str(TODAY + timedelta(days=14)),
            "check_out": str(TODAY + timedelta(days=17)),
        },
    )
    assert r.status_code == 409
    assert "Vietnam" in r.json()["detail"]
    assert "new trip for Thailand" in r.json()["detail"]


def test_a_leg_into_another_country_is_refused(client):
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10)
    r = client.post(
        f"/api/trips/{trip_id}/legs",
        json={"country_code": "TH", "to_place": "Bangkok"},
    )
    assert r.status_code == 409


def test_the_only_hotel_may_change_country(client):
    """That is correcting which country the trip is in, not splitting it."""
    trip_id = _mk_trip(client)
    detail = _stay_in(client, trip_id, "VN", "Hanoi", 10)
    stay_id = detail["country"]["stays"][0]["id"]

    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}",
        json={"country_code": "TH", "city": "Bangkok"},
    ).json()
    assert detail["country"]["country_code"] == "TH"
    # The old country's entry does not linger.
    assert detail["country"]["entry"]["country_code"] == "TH"


def test_one_of_several_hotels_may_not_change_country(client):
    trip_id = _mk_trip(client)
    detail = _stay_in(client, trip_id, "VN", "Hanoi", 10)
    _stay_in(client, trip_id, "VN", "Hue", 13)
    stay_id = detail["country"]["stays"][0]["id"]

    r = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}",
        json={"country_code": "TH", "city": "Bangkok"},
    )
    assert r.status_code == 409


def test_passport_choice_survives_an_unrelated_edit(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id)
    p = client.post(
        "/api/passports", json={"nationality": "US", "number_last4": "9032"}
    ).json()
    entry_id = detail["country"]["entry"]["id"]
    client.patch(
        f"/api/trips/{trip_id}/entries/{entry_id}",
        json={"passport_id": p["id"], "port_of_entry": "Noi Bai"},
    )

    stay_id = detail["country"]["stays"][0]["id"]
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}", json={"hotel_name": "Other hotel"}
    ).json()
    assert detail["country"]["passport_id"] == p["id"]
    assert detail["country"]["entry"]["port_of_entry"] == "Noi Bai"


def test_passport_defaults_to_the_last_one_used_for_that_country(client):
    p = client.post("/api/passports", json={"nationality": "US"}).json()
    first = _mk_trip(client)
    detail = _stay_in(client, first, "JP", "Osaka", -60)
    client.patch(
        f"/api/trips/{first}/entries/{detail['country']['entry']['id']}",
        json={"passport_id": p["id"]},
    )

    second = _mk_trip(client)
    detail = _stay_in(client, second, "JP", "Kyoto", 30)
    assert detail["country"]["passport_id"] == p["id"]


# --------------------------------------------------------------------------
# Labels and status
# --------------------------------------------------------------------------


def test_label_is_city_and_hotel_for_one_stop(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id, city="Hanoi", hotel_name="Sofitel Legend")
    assert detail["label"] == "Hanoi · Sofitel Legend"


def test_label_is_just_the_city_when_no_hotel_yet(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id, city="Hanoi", hotel_name="")
    assert detail["label"] == "Hanoi"


def test_label_truncates_a_long_route(client):
    trip_id = _mk_trip(client)
    for i, city in enumerate(["Hanoi", "Hue", "Hoi An", "Da Nang", "Da Lat"]):
        _stay_in(client, trip_id, "VN", city, 10 + i * 3, nights=3)
    detail = client.get(f"/api/trips/{trip_id}").json()
    assert detail["label"] == "Hanoi → Hue +3 more"


def test_trip_status(client):
    past = _mk_trip(client)
    _stay_in(client, past, "VN", "Hanoi", -20, nights=10)
    ongoing = _mk_trip(client)
    _stay_in(client, ongoing, "TH", "Bangkok", -2, nights=4)
    future = _mk_trip(client)
    _stay_in(client, future, "JP", "Osaka", 10)
    undated = _mk_trip(client)

    by_id = {t["id"]: t["status"] for t in client.get("/api/trips").json()}
    assert by_id[past] == "past"
    assert by_id[ongoing] == "ongoing"
    assert by_id[future] == "future"
    assert by_id[undated] == "undated"


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------


def test_patch_omitted_field_is_left_alone_but_null_clears(client):
    trip_id = _mk_trip(client)
    detail = _mk_stay(client, trip_id)
    stay_id = detail["country"]["stays"][0]["id"]

    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}", json={"city": "Da Nang"}
    ).json()
    assert detail["country"]["stays"][0]["hotel_name"] == "Sofitel Legend"
    assert detail["country"]["stays"][0]["city"] == "Da Nang"

    # An explicit null clears the field. Because `notes` is a non-nullable text
    # column it resets to its default rather than raising IntegrityError.
    detail = client.patch(
        f"/api/trips/{trip_id}/stays/{stay_id}",
        json={"hotel_name": None, "notes": None},
    ).json()
    assert detail["country"]["stays"][0]["hotel_name"] == ""
    assert detail["country"]["stays"][0]["notes"] == ""


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


def test_hotel_of_another_trip_is_404_not_403(client):
    a = _mk_trip(client)
    b = _mk_trip(client)
    stay_id = _mk_stay(client, a)["country"]["stays"][0]["id"]
    r = client.patch(f"/api/trips/{b}/stays/{stay_id}", json={"city": "Nope"})
    assert r.status_code == 404


def test_deleting_a_trip_takes_everything_with_it(client, session):
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
    then wrote `notes` again as the Note array, dropping the memo."""
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
    assert detail["notes"] == "Cherry blossom trip."
    assert [n["title"] for n in detail["notes_list"]] == ["Dentist in Hanoi"]


# --------------------------------------------------------------------------
# Nights with no hotel booked
# --------------------------------------------------------------------------


def _unbooked(client, trip_id):
    return client.get(f"/api/trips/{trip_id}").json()["country"]["unbooked"]


def test_gap_between_two_hotels(client):
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=3)
    _stay_in(client, trip_id, "VN", "Hue", 16, nights=4)

    assert _unbooked(client, trip_id) == [
        {
            "from": str(TODAY + timedelta(days=13)),
            "to": str(TODAY + timedelta(days=16)),
            "nights": 3,
        }
    ]


def test_no_gap_when_hotels_run_back_to_back(client):
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=3)
    _stay_in(client, trip_id, "VN", "Hue", 13, nights=3)
    assert _unbooked(client, trip_id) == []


def test_gap_between_landing_and_the_first_hotel(client):
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=4)
    client.post(
        f"/api/trips/{trip_id}/legs",
        json={
            "to_place": "Hanoi",
            "arrive_at": f"{TODAY + timedelta(days=9)}T21:30:00",
        },
    )
    assert _unbooked(client, trip_id) == [
        {
            "from": str(TODAY + timedelta(days=9)),
            "to": str(TODAY + timedelta(days=10)),
            "nights": 1,
        }
    ]


def test_overlapping_hotels_are_not_a_gap(client):
    """Double-booked is a different problem; it must not read as unbooked."""
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=5)
    _stay_in(client, trip_id, "VN", "Hue", 12, nights=2)
    assert _unbooked(client, trip_id) == []


def test_nothing_unbooked_before_a_leaving_date_is_set(client):
    """Without a departure date the stay can only end at the last checkout."""
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=4)
    assert _unbooked(client, trip_id) == []


def test_saying_when_you_leave_exposes_the_unbooked_tail(client):
    """Two weeks in Vietnam with only the first four nights booked -- the most
    common way to have forgotten a hotel, and invisible without this date."""
    trip_id = _mk_trip(client)
    detail = _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=4)
    entry_id = detail["country"]["entry"]["id"]

    detail = client.patch(
        f"/api/trips/{trip_id}/entries/{entry_id}",
        json={"exited_on": str(TODAY + timedelta(days=24))},
    ).json()

    assert detail["country"]["leaving_on"] == str(TODAY + timedelta(days=24))
    assert detail["country"]["unbooked"] == [
        {
            "from": str(TODAY + timedelta(days=14)),
            "to": str(TODAY + timedelta(days=24)),
            "nights": 10,
        }
    ]


def test_unbooked_nights_show_on_the_trip_list(client):
    trip_id = _mk_trip(client)
    _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=3)
    _stay_in(client, trip_id, "VN", "Hue", 16, nights=4)

    row = next(t for t in client.get("/api/trips").json() if t["id"] == trip_id)
    assert row["unbooked_nights"] == 3
    assert row["country_name"] == "Vietnam"


def test_the_leaving_date_extends_the_trip_span(client):
    """The stay is not over when the last hotel ends."""
    trip_id = _mk_trip(client)
    detail = _stay_in(client, trip_id, "VN", "Hanoi", 10, nights=4)
    assert detail["end_date"] == str(TODAY + timedelta(days=14))

    detail = client.patch(
        f"/api/trips/{trip_id}/entries/{detail['country']['entry']['id']}",
        json={"exited_on": str(TODAY + timedelta(days=24))},
    ).json()
    assert detail["end_date"] == str(TODAY + timedelta(days=24))
