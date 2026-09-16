# Calendar flight bands — plan

## Background / why this exists

On the calendar, the Kazakhstan country band began on **Sep 30**, the day the
London→Astana flight *departed London*, even though the flight does not *land in
Astana* until **Oct 1**. The band should not claim a day you were still in the
previous country.

Root cause: `refresh_trip_dates` (`backend/app/services/trips.py`) feeds **both**
a leg's `depart_at.date()` and `arrive_at.date()` into the trip's start/end
candidates. Every leg is an *arrival into this country* (return travel is not
modelled), so its departure happens in the **previous** country and must not
pull this trip's band earlier. The trip-**detail** path already does this right
(`trip_country`, uses `arrive_at or depart_at`); the denormalised
`trip.start_date`/`end_date` the calendar reads does not. The two disagree.

Beyond the fix, the user approved a **flight band**: a slim pill that spans the
flight's departure → arrival across days, bridging the seam between the origin
block and the destination block, replacing today's lone `✈` glyph-in-the-gap.

### Approved design (locked)

- **Placement:** its own slim lane in the seam, bridging origin end → destination
  start. Replaces the old glyph connector entirely.
- **Label priority (width-adaptive):** time is more important than airport code.
  Wide → `10:05p LHR ✈ NQZ 6:30a⁺¹`; constrained → **times only**
  (`10:05p ✈ 6:30a⁺¹`); tiny → plane glyph alone. Full detail (carrier, all
  flight numbers, both times, places) always on hover.
- **Connecting flights:** show a small connection icon on the band (e.g.
  `ti-transfer` / a dot-link glyph). No segment detail inline — the icon only
  signals "this is a connection". Signal = `Leg.number` holds more than one
  flight number (stored comma-separated by `_apply_leg`).
- **Times:** American 12-hour (`6:30a`, `10:05p`); `⁺¹` when arrival is a later
  calendar day than departure.
- **Modes:** generalises to train/bus/ferry/car by swapping the glyph, same as
  the existing `MODE_GLYPH` map.

## Assumptions (validate before trusting)

1. `refresh_trip_dates` is the only writer of `trip.start_date`/`end_date` that
   the calendar reads. (Grep `start_date =` / `end_date =` in backend.)
2. `arrival_mode` is consumed **only** by the calendar connector. (Confirmed:
   grep shows only `Calendar.tsx` + tests.) Safe to stop using it in the view.
3. A connecting flight is a single `Leg` whose `number` contains a comma. No
   dedicated segment table exists. (Confirmed in `_apply_leg`.)
4. `depart_at` / `arrive_at` are stored as **naive local** datetimes (the model
   comment says times are kept as-printed, no UTC conversion), so time-of-day
   can be read directly for horizontal positioning.

## Cross-cutting gotchas / risks

- **Windows / PowerShell:** run commands one per block. Tests run from
  `frontend/` (`npm test`) and `backend/` (pytest via the venv).
- **Missing times:** many legs have `depart_at` but no `arrive_at` (emails often
  omit arrival). The band must still render — fall back to a minimal pill at the
  departure position. Legs with neither time sit at day-midnight, min width.
- **Week clipping:** a flight can cross a week boundary (red-eye on Sat night).
  Clip per week like stays; keep the cut edge flush (`continuesLeft/Right`).
- **Min width:** a same-day 90-minute hop is a sliver. Enforce a clickable
  minimum (reuse the `MIN_SPAN` idea) without letting the label overflow.
- **Deploy needs an image rebuild** (frontend is baked into the image) — use
  `deploy/deploy.sh`, never a bare `git pull`. Verify the shipped bundle.
- **Don't over-remove `arrival_mode`:** leave the field in the payload/types so
  unrelated tests and any future consumer keep working; just stop reading it in
  the view.

---

## Phase 1 — Fix the trip-span so the band starts on arrival

**Objective:** the destination country band starts on the **arrival** date, not
the departure date. `refresh_trip_dates` collapses each leg to one instant.

**Assumptions to validate here:** #1, #4 above.

**Gotchas:** a leg with only `depart_at` must still contribute (fallback to
departure). Do not change `end_date` semantics for stays/leaving — only the leg
contribution.

**Changes**
- `backend/app/services/trips.py::refresh_trip_dates`: replace the two
  `if leg.depart_at / if leg.arrive_at` appends with a single
  `when = leg.arrive_at or leg.depart_at; if when: starts.append(when.date());
  ends.append(when.date())`. Mirror the wording already used in `trip_country`.

**Tests (must pass to proceed)**
- [x] `test_leg_span_starts_on_arrival_not_departure`: arrival (day 10) drives
      the start, not the day-9 departure, even with check-in on day 12.
- [x] `test_leg_with_only_departure_falls_back_to_departure_date`.
- [x] Existing `backend/tests/test_trips.py` all green (51). Full suite: 384.

- [x] **Committed** — hash: `0d250ce`

**Lesson:** the old test `test_leg_extends_the_span_before_the_first_checkin`
encoded the bug (asserted the departure day). Rewrote it rather than adding
alongside, so the suite no longer pins the wrong behavior.

---

## Phase 2 — Carry flight data to the calendar payload

**Objective:** `list_trips` sends the per-leg summary the band needs. Today it
sends only `arrival_mode` (a bare string).

**Assumptions to validate here:** #2, #3 above.

**Gotchas:** keep the payload lean (fetched every page load) — send only what a
band draws. Keep `arrival_mode` in place (don't break other tests).

**Changes**
- `backend/app/api/trips.py::list_trips`: add a `legs` array, each item:
  `id, mode, country_code, from_place, from_iata, to_place, to_iata,
  depart_at (iso|null), arrive_at (iso|null), is_connection (bool = number has
  a comma), number`. Order by `depart_at`.
- Consider a small helper for the `is_connection` test so it is unit-testable.

**Tests (must pass to proceed)**
- [x] `test_the_trip_list_carries_legs_for_the_flight_band` (route + ISO times).
- [x] `test_a_connecting_leg_is_flagged` (comma number → `is_connection: true`).
- [x] Full backend suite green (386).

- [x] **Committed** — hash: `3ee7d7f`

**Note:** `is_connection` lives in `leg_is_connection` (services/trips.py) so it
is importable and unit-testable; the API test exercises it end to end.

---

## Phase 3 — Draw the flight band (frontend)

**Objective:** render the approved band; delete the glyph connector.

**Gotchas:** all the cross-cutting risks land here (missing times, week
clipping, min width, label degradation). Build the label as a priority list so
width drives what shows.

**Changes**
- `frontend/src/types.ts`: add `LegSummary` (mirrors Phase 2) and
  `legs: LegSummary[]` on `TripSummary`.
- `frontend/src/lib/format.ts` (or nearby): a `clockShort(iso)` →
  `6:30a` / `10:05p`, and a helper for the `⁺¹` day-offset marker.
- `frontend/src/views/Calendar.tsx`:
  - New `FlightBand` layout type: horizontal extent from departure instant to
    arrival instant, using `dayIndex + hour/24` for time-of-day; clipped to the
    week; `continuesLeft/Right` like stays; min width enforced.
  - Vertical: a slim lane (≈16px, `< LANE`) anchored to the destination group's
    top edge, sitting in the seam. Reserve the sliver so country blocks don't
    overlap it.
  - Label: build from a priority list [dep time, plane/mode glyph, arr time+⁺¹,
    airport codes]; drop codes first, then times, then all but the glyph, based
    on available px. Connection icon appended when `is_connection`.
  - `title` tooltip: carrier, `number` (all segments), `from_place (from_iata)
    HH:MMa → to_place (to_iata) HH:MMa⁺¹`.
  - **Remove** the `connectors` / `connectBoundaries` glyph rendering and the
    `arrival_mode` read. Keep `MODE_GLYPH`.

**Tests (must pass to proceed)** — `frontend/src/views/Calendar.test.tsx`
- [x] Band shows departure and arrival **times** (`10:05p`, `6:30a⁺1`).
- [x] Band bridges from the departure side and docks at the block, never over it.
- [x] Codes appear only on a wide band; connection icon on `is_connection`, not
      otherwise; a depart-only leg still renders; no `.cal-hop` remains.
- [x] `npm run lint` clean; full `npm test` green (65).

- [x] **Committed** — hash: `(this commit)`

**Design change during build:** the seam between two back-to-back trips (London
ends the day Kazakhstan starts) is too narrow to hold a readable band. So each
inbound flight now gets its **own slim strip** reserved at the top of its
destination block (`flightLanes`), guaranteeing room for the times regardless of
what abuts it. The band still docks its arrival edge into the block.

**Backfill migration** (`a1c7e9f2b3d4`): `refresh_trip_dates` only reruns when a
trip is touched, so existing trips (Kazakhstan) kept the stale Sep-30 start. A
data migration recomputes every trip's span with the corrected rule; verified
locally — Kazakhstan flips to Oct 1 and the band shows
`2:40p ✈⇢ 4:45a⁺1` (Pegasus PC1162, PC228).

---

## Phase 4 — Deploy, verify live, document

**Objective:** ship it and prove the running app shows the band on the real
Kazakhstan trip.

**Steps**
1. [ ] Ask the user to `git push` (push is normally blocked for the agent; this
       session may be able to push — try, else ask).
2. [ ] `ssh yayokun@5.78.184.240 'cd /srv/yayo_travel_tracker && ./deploy/deploy.sh'`
3. [ ] Verify the shipped bundle contains a new marker string (grep the built
       `/assets/index-*.js`, per README §4).
4. [ ] Visually confirm: KZ band starts Oct 1 and the London→Astana band bridges
       the seam with times.
5. [ ] README §5 (or the calendar section): document the band, the arrival-date
       fix, and the `is_connection` heuristic. Record all phase hashes here.

- [ ] **Committed** — hash: `________`

---

## Lessons learned (fill in as you go)

- _(Phase 1)_ …
- _(Phase 2)_ …
- _(Phase 3)_ …
- _(Phase 4)_ …
