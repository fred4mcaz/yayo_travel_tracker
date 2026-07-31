"""Geocoding tests.

These read the real bundled dataset, so they double as a check that
scripts/build_geo.py produced something usable.
"""

from datetime import date, timedelta

import pytest

from app.services.geocode import fill_coordinates, locate, normalise

TODAY = date.today()

pytestmark = pytest.mark.skipif(
    not locate("VN", "Hanoi"),
    reason="data/geo/cities.min.json not built; run scripts/build_geo.py",
)


@pytest.mark.parametrize(
    "country,city,lat,lon",
    [
        ("VN", "Hanoi", 21.0, 105.8),
        ("TH", "Bangkok", 13.7, 100.5),
        ("JP", "Osaka", 34.7, 135.5),
        ("MX", "Guadalajara", 20.7, -103.3),
        ("US", "Phoenix", 33.4, -112.1),
    ],
)
def test_known_cities_resolve(country, city, lat, lon):
    found = locate(country, city)
    assert found is not None, f"{city}, {country} not found"
    assert abs(found[0] - lat) < 0.6
    assert abs(found[1] - lon) < 0.6


def test_lookup_is_case_and_space_insensitive():
    assert locate("VN", "  HANOI ") == locate("VN", "hanoi")


def test_trailing_qualifiers_are_stripped():
    """People type what they see on a booking: "Hanoi, Vietnam"."""
    assert locate("VN", "Hanoi, Vietnam") == locate("VN", "Hanoi")
    assert locate("TH", "Bangkok (BKK)") == locate("TH", "Bangkok")


def test_saint_abbreviation_falls_back():
    assert locate("RU", "St Petersburg") == locate("RU", "Saint Petersburg")


def test_city_must_match_its_country():
    """Bangkok is not in Vietnam; a wrong country must not silently resolve."""
    assert locate("VN", "Bangkok") is None


def test_unknown_city_returns_none():
    assert locate("VN", "Nowheresville") is None
    assert locate("", "Hanoi") is None
    assert locate("VN", "") is None


def test_normalise():
    assert normalise("  São Paulo ") == "são paulo"
    assert normalise("Washington, D.C.") == "washington"


# --- integration with the API -------------------------------------------


def test_creating_a_stay_derives_its_pin(client):
    trip = client.post("/api/trips", json={"title": "Vietnam"}).json()
    detail = client.post(
        f"/api/trips/{trip['id']}/stays",
        json={
            "country_code": "VN",
            "city": "Hanoi",
            "check_in": str(TODAY),
            "check_out": str(TODAY + timedelta(days=3)),
        },
    ).json()
    stay = detail["stays"][0]
    assert stay["lat"] is not None and stay["lon"] is not None
    assert abs(stay["lat"] - 21.0) < 0.6


def test_moving_a_stay_moves_its_pin(client):
    trip = client.post("/api/trips", json={"title": "Asia"}).json()
    detail = client.post(
        f"/api/trips/{trip['id']}/stays",
        json={
            "country_code": "VN",
            "city": "Hanoi",
            "check_in": str(TODAY),
            "check_out": str(TODAY + timedelta(days=3)),
        },
    ).json()
    stay_id = detail["stays"][0]["id"]
    original = detail["stays"][0]["lat"]

    detail = client.patch(
        f"/api/trips/{trip['id']}/stays/{stay_id}",
        json={"country_code": "TH", "city": "Bangkok"},
    ).json()
    moved = detail["stays"][0]
    assert moved["lat"] != original
    assert abs(moved["lat"] - 13.7) < 0.6


def test_editing_an_unrelated_field_keeps_the_pin(client):
    trip = client.post("/api/trips", json={"title": "Vietnam"}).json()
    detail = client.post(
        f"/api/trips/{trip['id']}/stays",
        json={
            "country_code": "VN",
            "city": "Hanoi",
            "check_in": str(TODAY),
            "check_out": str(TODAY + timedelta(days=3)),
        },
    ).json()
    stay_id = detail["stays"][0]["id"]
    before = detail["stays"][0]["lat"]

    detail = client.patch(
        f"/api/trips/{trip['id']}/stays/{stay_id}", json={"hotel_name": "Sofitel"}
    ).json()
    assert detail["stays"][0]["lat"] == before


def test_explicit_coordinates_are_never_overwritten(client):
    """A precise pin from an email address beats a city centroid."""

    class FakeStay:
        country_code = "VN"
        city = "Hanoi"
        lat = 21.0245
        lon = 105.8412

    stay = FakeStay()
    assert fill_coordinates(stay) is False
    assert stay.lat == 21.0245


def test_unknown_city_leaves_stay_unpinned(client):
    trip = client.post("/api/trips", json={"title": "Somewhere"}).json()
    detail = client.post(
        f"/api/trips/{trip['id']}/stays",
        json={
            "country_code": "VN",
            "city": "Tiny Hamlet Nobody Indexed",
            "check_in": str(TODAY),
            "check_out": str(TODAY + timedelta(days=1)),
        },
    ).json()
    # Not an error: the stay is still valid, it just has no pin.
    assert detail["stays"][0]["lat"] is None
