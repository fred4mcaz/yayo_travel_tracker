# Static data

Committed, read-only reference data baked into the container image. Anything
mutable lives in `var/` instead.

These directories must contain at least one tracked file. Git does not track
empty directories, so an empty `data/geo/` simply would not exist in a fresh
clone — and `COPY data/ /srv/data/` in the Dockerfile then fails the build with
a bare `"/data": not found`.

| Path | Contents | Added in |
|---|---|---|
| `geo/countries.min.geojson` | Natural Earth 110m admin-0 boundaries, simplified. Drives the country fills on the map. | Stage 5 |
| `geo/cities.min.json` | GeoNames subset (population > 15k) giving lat/lon per city, so typing a city name places a pin with no geocoding API. | Stage 5 |
| `rules/entry-requirements.json` | Per-country paperwork: which countries need an entry card, e-visa, or ETA. Drives the requirement checklist. | Stage 6 |
| `rules/visa-free.json` | Permitted visa-free days per country for MX and US passports, with a `last_updated` stamp. | Stage 6 |

`visa-free.json` is **advisory only**. Visa rules change without notice and a
stale JSON file on a personal server is not an authority. The UI labels it as
such. Confirm with the destination's consulate before travelling.
