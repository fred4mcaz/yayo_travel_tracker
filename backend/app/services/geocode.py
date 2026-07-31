"""City name to coordinates, from a bundled dataset.

The entry form only asks for a country and a city, so the map needs to derive
coordinates from those two strings. A local lookup means no geocoding API, no
key, no rate limit, no third party learning your travel plans, and it works
offline. The trade-off is coverage: cities under ~15,000 people are absent, and
those stays simply carry no pin until coordinates are set by hand or arrive from
a confirmation email.

Regenerate the dataset with `python scripts/build_geo.py`.
"""

import json
import logging
import re
from functools import lru_cache
from typing import Optional

from app.config import get_settings

log = logging.getLogger("yayo.geocode")
settings = get_settings()

# Strip the qualifiers that get typed but are not part of the name in GeoNames:
# "Hanoi, Vietnam" -> "hanoi", "Bangkok (BKK)" -> "bangkok".
_TRAILING = re.compile(r"\s*[,(].*$")
_PUNCT = re.compile(r"[^\w\s'-]", re.UNICODE)


@lru_cache(maxsize=1)
def _lookup() -> dict[str, list]:
    """Load the city table once, on first use.

    Roughly 40k entries. Each value is [lat, lon, display_name, population].
    Loading it lazily keeps it out of memory entirely for a process that never
    geocodes anything.
    """
    path = settings.data_dir / "geo" / "cities.min.json"
    if not path.is_file():
        log.warning("city dataset missing at %s; stays will have no pins", path)
        return {}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    log.info("loaded %d city names for geocoding", len(data))
    return data


@lru_cache(maxsize=1)
def _by_country() -> dict[str, list[tuple[str, str, float, float, int]]]:
    """Per-country index for autocomplete: (searchable, display, lat, lon, pop).

    Built once from the flat table. Scanning one country's few hundred entries
    per keystroke is far cheaper than filtering all 40k.
    """
    index: dict[str, list[tuple[str, str, float, float, int]]] = {}
    for key, value in _lookup().items():
        country, _, name = key.partition(":")
        if not country or len(value) < 4:
            continue
        lat, lon, display, population = value[0], value[1], value[2], value[3]
        index.setdefault(country, []).append((name, display, lat, lon, population))
    for entries in index.values():
        entries.sort(key=lambda e: -e[4])
    return index


def normalise(city: str) -> str:
    city = _TRAILING.sub("", city.strip())
    city = _PUNCT.sub("", city)
    return " ".join(city.lower().split())


def locate(country_code: str, city: str) -> Optional[tuple[float, float]]:
    """Coordinates for a city within a country, or None if not found."""
    if not country_code or not city:
        return None
    table = _lookup()
    if not table:
        return None

    name = normalise(city)
    if not name:
        return None

    found = table.get(f"{country_code.upper()}:{name}")
    if found is None:
        # "St Petersburg" vs "Saint Petersburg" is the common near-miss.
        for a, b in (("st ", "saint "), ("saint ", "st ")):
            if name.startswith(a):
                found = table.get(f"{country_code.upper()}:{b}{name[len(a):]}")
                if found is not None:
                    break
    if found is None:
        return None
    return found[0], found[1]


def suggest(country_code: str, query: str, limit: int = 8) -> list[dict]:
    """City suggestions within a country, most populous first.

    Prefix matches rank above substring matches, so typing "ha" in Vietnam
    offers Hanoi and Hạ Long before Thanh Hoa. Results are deduplicated by
    display name because a city is indexed under both its local and ASCII
    spelling and should only be offered once.
    """
    if not country_code:
        return []
    entries = _by_country().get(country_code.upper())
    if not entries:
        return []

    needle = normalise(query)
    prefix: list[tuple[str, float, float]] = []
    contains: list[tuple[str, float, float]] = []
    seen: set[str] = set()

    for searchable, display, lat, lon, _pop in entries:
        if needle and not searchable.startswith(needle):
            if needle not in searchable:
                continue
            bucket = contains
        else:
            bucket = prefix
        if display in seen:
            continue
        seen.add(display)
        bucket.append((display, lat, lon))
        if len(prefix) >= limit:
            break

    combined = (prefix + contains)[:limit]
    return [{"name": n, "lat": lat, "lon": lon} for n, lat, lon in combined]


def fill_coordinates(stay) -> bool:
    """Set lat/lon on a stay from its city, if they are not already known.

    Returns whether anything changed. Never overwrites existing coordinates:
    those may have been set deliberately or extracted from a booking email with
    a precise address, and a city centroid would be a downgrade.
    """
    if stay.lat is not None and stay.lon is not None:
        return False
    found = locate(stay.country_code, stay.city)
    if found is None:
        return False
    stay.lat, stay.lon = found
    return True
