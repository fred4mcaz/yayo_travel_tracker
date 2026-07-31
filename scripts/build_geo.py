#!/usr/bin/env python3
"""Regenerate the bundled geo data in data/geo/.

    python scripts/build_geo.py

Downloads the upstream sources, strips them to what the app actually needs, and
writes two files. The sources are deliberately not committed -- they are 4MB of
data we use maybe 8% of, and this script makes the result reproducible.

Outputs:
  data/geo/countries.min.geojson  Country outlines, served to the browser for
                                  the map. Coordinates rounded to 2dp (~1km),
                                  which is far finer than a world map renders.
  data/geo/cities.min.json        City -> lat/lon lookup, used server-side to
                                  place a pin from a typed city name without
                                  calling a geocoding API.
"""

import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "geo"

COUNTRIES_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_110m_admin_0_countries.geojson"
)
CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"

COORD_PRECISION = 2  # decimal places; ~1.1km at the equator
CITY_PRECISION = 4  # ~11m, ample for a map pin


def fetch(url: str) -> bytes:
    print(f"  fetching {url}")
    with urllib.request.urlopen(url, timeout=300) as response:
        return response.read()


# --------------------------------------------------------------------------
# Countries
# --------------------------------------------------------------------------


def country_code(props: dict) -> str | None:
    """Best available ISO 3166-1 alpha-2 for a Natural Earth feature.

    ISO_A2 is -99 for five features. ISO_A2_EH fills in Norway, France, and
    Kosovo. The remaining two (Northern Cyprus, Somaliland) have no ISO code of
    their own, so they fall back to Natural Earth's own ADM0_ISO sovereign
    parent -- deferring to the dataset rather than making that call ourselves.
    """
    for key in ("ISO_A2_EH", "ISO_A2"):
        value = props.get(key)
        if value and value != "-99" and len(value) == 2:
            return value

    parent = props.get("ADM0_ISO")
    return {"CYP": "CY", "SOM": "SO"}.get(parent)


def round_ring(ring: list, precision: int) -> list:
    """Round coordinates and drop points that collapse onto their neighbour."""
    out: list = []
    for point in ring:
        rounded = [round(point[0], precision), round(point[1], precision)]
        if not out or rounded != out[-1]:
            out.append(rounded)
    # A polygon ring must close and needs at least 3 distinct points.
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return out


def simplify_geometry(geometry: dict) -> dict | None:
    kind = geometry["type"]
    if kind == "Polygon":
        rings = [round_ring(r, COORD_PRECISION) for r in geometry["coordinates"]]
        rings = [r for r in rings if len(r) >= 4]
        return {"type": "Polygon", "coordinates": rings} if rings else None

    if kind == "MultiPolygon":
        polys = []
        for poly in geometry["coordinates"]:
            rings = [round_ring(r, COORD_PRECISION) for r in poly]
            rings = [r for r in rings if len(r) >= 4]
            if rings:
                polys.append(rings)
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None

    return None


def build_countries() -> None:
    print("Countries:")
    raw = json.loads(fetch(COUNTRIES_URL).decode("utf-8"))

    features = []
    skipped = []
    for feature in raw["features"]:
        props = feature["properties"]
        code = country_code(props)
        name = props.get("NAME_EN") or props.get("NAME") or props.get("ADMIN")
        if not code:
            skipped.append(name)
            continue
        geometry = simplify_geometry(feature["geometry"])
        if geometry is None:
            skipped.append(name)
            continue
        # Two-letter keys: this file is downloaded by every map view.
        features.append(
            {"type": "Feature", "properties": {"c": code, "n": name}, "geometry": geometry}
        )

    out = {"type": "FeatureCollection", "features": features}
    path = OUT_DIR / "countries.min.geojson"
    path.write_text(
        json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    size = path.stat().st_size
    print(f"  {len(features)} countries -> {path.name} ({size / 1024:.0f} KB)")
    if skipped:
        print(f"  skipped {len(skipped)}: {', '.join(str(s) for s in skipped)}")


# --------------------------------------------------------------------------
# Cities
# --------------------------------------------------------------------------


def build_cities() -> None:
    print("Cities:")
    archive = zipfile.ZipFile(io.BytesIO(fetch(CITIES_URL)))

    # Key is "CC:lowercased name" so lookups are exact and cheap. Where two
    # cities in one country share a name, the more populous one wins -- if you
    # type "Springfield, US" you almost certainly mean the big one.
    best: dict[str, tuple[int, float, float]] = {}

    with archive.open("cities15000.txt") as handle:
        for line in io.TextIOWrapper(handle, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 15:
                continue
            name, ascii_name = parts[1], parts[2]
            lat, lon = float(parts[4]), float(parts[5])
            country = parts[8]
            population = int(parts[14] or 0)
            if not country:
                continue

            for label in {name, ascii_name}:
                key = f"{country}:{label.strip().lower()}"
                existing = best.get(key)
                if existing is None or population > existing[0]:
                    best[key] = (population, lat, lon)

    lookup = {
        key: [round(lat, CITY_PRECISION), round(lon, CITY_PRECISION)]
        for key, (_, lat, lon) in sorted(best.items())
    }

    path = OUT_DIR / "cities.min.json"
    path.write_text(
        json.dumps(lookup, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    size = path.stat().st_size
    print(f"  {len(lookup)} names -> {path.name} ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        build_countries()
        build_cities()
    except urllib.error.URLError as exc:  # pragma: no cover
        print(f"error: could not download source data: {exc}", file=sys.stderr)
        sys.exit(1)
    print("Done.")
