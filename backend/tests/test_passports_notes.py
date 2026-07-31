from datetime import date, timedelta

TODAY = date.today()


def test_full_passport_number_is_rejected_not_truncated(client):
    """Silently truncating would leave the user believing it was stored."""
    r = client.post(
        "/api/passports", json={"nationality": "MX", "number_last4": "G12345678"}
    )
    assert r.status_code == 422
    assert "last 4" in r.text


def test_last4_accepted(client):
    r = client.post(
        "/api/passports", json={"nationality": "MX", "number_last4": "4471"}
    )
    assert r.status_code == 201
    assert r.json()["number_last4"] == "4471"


def test_only_one_default_passport(client):
    a = client.post(
        "/api/passports", json={"nationality": "MX", "is_default": True}
    ).json()
    b = client.post(
        "/api/passports", json={"nationality": "US", "is_default": True}
    ).json()
    defaults = [p["id"] for p in client.get("/api/passports").json() if p["is_default"]]
    assert defaults == [b["id"]]
    assert a["id"] not in defaults


def test_deleting_passport_keeps_the_entry_history(client):
    """The trip record is the valuable part; the document is an attribute."""
    trip = client.post("/api/trips", json={"title": "Japan"}).json()
    client.post(
        f"/api/trips/{trip['id']}/stays",
        json={
            "country_code": "JP",
            "city": "Osaka",
            "check_in": str(TODAY - timedelta(days=30)),
            "check_out": str(TODAY - timedelta(days=25)),
        },
    )
    p = client.post("/api/passports", json={"nationality": "US"}).json()
    detail = client.get(f"/api/trips/{trip['id']}").json()
    entry_id = detail["entries"][0]["id"]
    client.patch(
        f"/api/trips/{trip['id']}/entries/{entry_id}", json={"passport_id": p["id"]}
    )

    assert client.delete(f"/api/passports/{p['id']}").status_code == 204

    detail = client.get(f"/api/trips/{trip['id']}").json()
    assert len(detail["entries"]) == 1
    assert detail["entries"][0]["passport_id"] is None
    assert detail["entries"][0]["country_code"] == "JP"


def test_country_history_across_trips(client):
    p = client.post("/api/passports", json={"nationality": "US"}).json()
    for n in (60, 20):
        trip = client.post("/api/trips", json={"title": f"JP {n}"}).json()
        client.post(
            f"/api/trips/{trip['id']}/stays",
            json={
                "country_code": "JP",
                "city": "Osaka",
                "check_in": str(TODAY - timedelta(days=n)),
                "check_out": str(TODAY - timedelta(days=n - 5)),
            },
        )
        detail = client.get(f"/api/trips/{trip['id']}").json()
        client.patch(
            f"/api/trips/{trip['id']}/entries/{detail['entries'][0]['id']}",
            json={"passport_id": p["id"]},
        )

    history = client.get("/api/passports/history/jp").json()
    assert len(history) == 2
    # Most recent first.
    assert history[0]["entered_on"] > history[1]["entered_on"]
    assert history[0]["passport_nationality"] == "US"


def test_standalone_note_needs_no_trip(client):
    r = client.post(
        "/api/notes",
        json={"on_date": str(TODAY + timedelta(days=20)), "title": "Renew passport"},
    )
    assert r.status_code == 201
    assert r.json()["trip_id"] is None


def test_note_search_and_date_filter(client):
    client.post(
        "/api/notes",
        json={
            "on_date": str(TODAY),
            "title": "Dentist in Osaka",
            "body": "Dr. Tanaka",
        },
    )
    client.post(
        "/api/notes",
        json={"on_date": str(TODAY + timedelta(days=90)), "title": "Something else"},
    )

    assert len(client.get("/api/notes?q=Tanaka").json()) == 1
    # SQLite LIKE is case-insensitive for ASCII, which is what search should do.
    assert len(client.get("/api/notes?q=dentist").json()) == 1
    assert len(client.get("/api/notes?q=DENTIST").json()) == 1
    # Body is searched too, not just the title.
    assert len(client.get("/api/notes?q=tanaka").json()) == 1
    windowed = client.get(
        f"/api/notes?date_from={TODAY}&date_to={TODAY + timedelta(days=7)}"
    ).json()
    assert len(windowed) == 1
    assert windowed[0]["title"] == "Dentist in Osaka"


def test_note_survives_trip_deletion(client):
    trip = client.post("/api/trips", json={"title": "Japan"}).json()
    client.post(
        "/api/notes",
        json={
            "trip_id": trip["id"],
            "on_date": str(TODAY),
            "title": "Dentist in Osaka",
        },
    )
    client.delete(f"/api/trips/{trip['id']}")
    notes = client.get("/api/notes").json()
    assert len(notes) == 1
    assert notes[0]["trip_id"] is None
