# Booking detail extraction — capturing flight numbers, airports, times, seats

## Why this plan exists

The Gmail extractor reads a booking email correctly but **throws most of it
away**. A real Pegasus confirmation (`PC1162` London-Stansted → Istanbul →
Astana, seats `10A-11A`, depart `14:40`, arrive `04:45`) lands in the app as a
bare "flight arriving into KZ" with no flight number, no airports, no times, no
seat.

The cause is **not the model** — it reads the full body fine. It is the
**tool schema**: [`BOOKING_ITEM_SCHEMA`](backend/app/services/extraction.py)
has only 8 slots (`kind, country_code, city, start_date, end_date, hotel_name,
carrier, confirmation_code`), so everything else is dropped before it is ever
stored. The database columns already exist and sit empty:
[`Leg`](backend/app/models.py) has `number`, `from_place`, `from_iata`,
`to_place`, `to_iata`, `depart_at`, `arrive_at`, `seat` — none of them filled by
[`_apply_leg`](backend/app/services/review.py).

Fixing this means widening **one chain, end to end**:

```
schema  →  Booking dataclass  →  validate_booking  →  Booking.payload()
        →  _apply_leg (accept mapping)  →  ALLOWED_OVERRIDES  →  UI
```

Change one link without the others and the field is silently lost again — the
exact failure mode we just fixed with the JSON-string bug.

## Decisions already made (do not re-litigate)

1. **Connecting flights collapse to one arrival-into-country, but keep every
   segment's flight number.** A trip is one arrival into one country (the core
   domain invariant — see README §1). So `STN→SAW→NQZ` is **one** leg arriving
   into **KZ**, with:
   - `from_*` = the true origin of the whole journey (`STN`, London-Stansted)
   - `to_*` = the final destination (`NQZ`, Astana)
   - `depart_at` = first departure (`2026-09-29 14:40`)
   - `arrive_at` = final arrival (`2026-09-30 04:45`)
   - `number` = **all** operating flight numbers, joined (`PC1162, PC228`)
2. **Travel essentials only.** Capture for legs: flight/train number,
   origin + IATA, destination + IATA, depart datetime, arrive datetime, seat.
   **Out of scope for now:** cost, currency, hotel address. Hotels already
   capture their essentials; this plan is leg-centric.

## Cross-cutting gotchas (true for every phase)

- **Times are naive local wall-clock, by design** (see `models.py` header). The
  email prints `14:40` at STN and `04:45` at NQZ — two different timezones,
  stored as-printed with **no conversion**. Do not "correct" them to UTC.
- **`_at()` already parses datetimes.** `datetime.fromisoformat` accepts both
  `2026-09-29` and `2026-09-29T14:40`, so a full datetime flows through the
  accept path with no new helper.
- **Keep good fields when one is malformed.** `validate_booking` must validate
  each new field independently and null out a bad one, never reject the whole
  booking over a stray seat string. This is the "not missed" principle.
- **The year anchor still applies.** `correct_year` keys off `start_date`.
  Keep `start_date`/`end_date` as the canonical date anchor; `depart_at`/
  `arrive_at` are additive detail, not a replacement.
- **Nullable everywhere.** Every new schema field allows `null` so the model
  states "not present" explicitly rather than inventing a seat that isn't there.

---

## Phase 1 — Widen the schema, the `Booking` model, and validation

**Objective:** the extractor *captures* every essential field into a validated
`Booking`, without yet changing what gets stored. End of phase: a fake model
returning full detail round-trips through `validate_bookings` intact.

**Assumptions to validate first:**
- [x] `BOOKING_ITEM_SCHEMA` is the only schema the extract tool uses (grep for
      other `input_schema` on the extract path). — confirmed: only `EXTRACT_TOOL`.
- [x] `Booking.payload()` is what gets JSON-stored in `extraction.payload_json`
      (confirm in `_record_bookings`). — confirmed.
- [x] Nothing reads `Booking` fields positionally (it's a frozen dataclass with
      keyword construction everywhere). — confirmed: only construction site is
      `validate_booking`.

**Common problems to prepare for:**
- Strict tool schema requires **every** property in `required` and
  `additionalProperties: false` — add each new field to *both* the properties
  and the `required` list, expressing optionality as `["string", "null"]`.
- Adding a field to the frozen `Booking` dataclass without updating every
  construction site (`validate_booking`, tests, `replace()` in `correct_year`)
  raises `TypeError`. Give each new field a default of `None`.
- A `number` that is a list from the model vs. a string in the column — decide
  the seam here: schema captures `flight_numbers` as an array of strings; the
  accept mapper (Phase 2) joins them into `Leg.number`.

**Tasks:**
- [x] Extend `BOOKING_ITEM_SCHEMA` with: `flight_numbers` (array of strings),
      `from_place`, `from_iata`, `to_place`, `to_iata`, `depart_at`
      (ISO datetime), `arrive_at` (ISO datetime), `seat`. All nullable; arrays
      default to `[]`.
- [x] Rewrite the schema `description`s to encode Decision 1: for a connecting
      journey, record ONE arrival into the destination country, `from_*` = whole
      journey's origin, `to_*` = final destination, `depart_at`/`arrive_at` =
      first departure / final arrival, `flight_numbers` = every operating
      segment number in order.
- [x] Add the fields to the `Booking` dataclass (defaults `None` / `()`), and to
      `Booking.payload()`.
- [x] Extend `validate_booking`: IATA = exactly 3 alpha (upper-cased) else null;
      `depart_at`/`arrive_at` parsed with `datetime.fromisoformat` else null;
      `flight_numbers` coerced to a clean tuple of non-empty strings; `seat`
      trimmed. A bad single field nulls that field only.
- [x] Confirm `correct_year`'s `replace(...)` still compiles with the widened
      dataclass (it only touches `start_date`/`end_date`).

**Tests that must pass to proceed:**
- [x] `validate_booking` round-trips a full-detail flight (numbers, IATAs,
      datetimes, seat).
- [x] A malformed IATA (`"LONDON"`) / bad datetime nulls that field but keeps
      the booking.
- [x] `flight_numbers: ["PC1162","PC228"]` survives; `[]` and `null` both OK.
- [x] Existing `test_extraction.py` suite still green (no regressions).
      **356 backend tests pass (+9 new), ruff clean.**

**Commit:** `fd7f8c3`

---

## Phase 2 — Map the captured fields onto the `Leg` on accept

**Objective:** accepting a flight proposal writes the flight number(s),
airports, times, and seat onto the real `Leg`. This is the phase the user sees.

**Assumptions to validate first:**
- [x] `_apply_leg` is the only place a booking becomes a `Leg`
      (grep `Leg(` in services). — confirmed.
- [x] `ALLOWED_OVERRIDES` gates what a reviewer may correct in the Review form;
      a field not listed there cannot be edited before accept. — confirmed, AND
      found a second gate: `AcceptPayload` in `api/review.py` must also declare
      the field or pydantic drops it before it ever reaches ALLOWED_OVERRIDES.

**Common problems to prepare for:**
- If a new field is written by `_apply_leg` but missing from
  `ALLOWED_OVERRIDES`, the reviewer can see but not fix a model mistake — widen
  both together.
- `depart_at` currently comes from `_at(booking.start_date)` (date, no time).
  Prefer `booking.depart_at` when present, fall back to `_at(start_date)`.
- Joining `flight_numbers` → `Leg.number`: use `", ".join(...)`; empty list →
  `""` (the column default), never the string `"None"`.

**Tasks:**
- [x] `_apply_leg`: set `number` (joined `flight_numbers`), `from_place`,
      `from_iata`, `to_place` (falls back to `city`), `to_iata`, `arrive_at`,
      `seat`; `depart_at` = `_at(booking.depart_at) or _at(booking.start_date)`.
- [x] Add the new booking fields to `ALLOWED_OVERRIDES`.
- [x] Add the new fields to `AcceptPayload` (the API override schema) — the
      second gate found above; `flight_numbers` typed as `Optional[list[str]]`.
- [x] Leave `_apply_hotel` unchanged (hotel address/cost out of scope).

**Tests that must pass to proceed:**
- [x] Accepting a full-detail flight extraction produces a `Leg` with number,
      both IATAs, both datetimes, and seat populated.
- [x] A booking with only `start_date` (no `depart_at`) still sets `depart_at`
      to midnight of that date (back-compat — existing test still green).
- [x] Override of leg detail (incl. the `flight_numbers` list) before accept
      lands on the `Leg`, via both the service and the HTTP API.
- [x] `to_place` falls back to `city` when the model gave only the city.
- [x] Row-count boundary test still holds: nothing writes without accept.
      **360 backend tests pass (+4 new), ruff clean.**

**Commit:** `bd49e12`

---

## Phase 3 — Coverage: feed the automatic path the full body

**Objective:** the 10-minute poll ("Check email now") sees the *whole* email,
not the 400-char snippet, so second legs / seats / baggage beyond char 400 are
no longer invisible to the automatic path. (The manual path already re-fetches.)

**Why:** `process_email` uses `email.snippet` (400 chars). Everything past the
first few lines — the connecting segment, seat, baggage — is truncated, so the
auto path structurally cannot capture it even after Phases 1–2.

**Assumptions to validate first:**
- [x] `run_extractions` processes a batch; opening one IMAP connection for the
      batch (not one per email) is feasible with `ImapMailbox` as a context
      manager. — done via `imap_body_fetcher()` (one login per batch).
- [x] The privacy model is preserved: we still **store** only the 400-char
      snippet; the full body is used transiently at extraction time, which is
      already the point where mail leaves the box (README §5). — confirmed:
      `process_email` never writes the fetched body; only `email.snippet`
      (set at ingest) is persisted.

**Common problems to prepare for:**
- N emails × one IMAP login each = slow and rate-limit-prone. Open the mailbox
  once per `run_extractions` batch and re-fetch each by Message-ID.
- Re-fetch failure must **degrade to the snippet**, never fail the poll (mirror
  the manual path's `try/except` in `api/review.py`).
- Offline / IMAP-unconfigured tests must still pass — keep the model and
  mailbox behind their existing Protocols and inject fakes.

**Tasks:**
- [x] Give `process_email` / `run_extractions` an optional `fetch_body`; when
      present, re-fetch full body per email (feeds both triage and extraction),
      else use `email.snippet`.
- [x] Add `imap_body_fetcher()` context manager (in `email_ingest.py`) that
      opens ONE IMAP login for the batch and degrades to a None-fetcher if the
      mailbox will not open.
- [x] Wire it into the scheduler's `run_poll_cycle`.
- [x] Log a warning when a re-fetch fails / the fetcher is unavailable, so a
      silent degrade to snippet is visible.

**Tests that must pass to proceed:**
- [x] `process_email` uses the re-fetched body (triage + extract) when the
      fetcher returns one.
- [x] Fetcher returning None / raising → falls back to snippet, still extracts.
- [x] `run_extractions` threads the fetcher to every email in the batch.
- [x] `imap_body_fetcher` returns the full body via a fake box, and degrades to
      None when the mailbox will not open. No socket opened in the suite.
      **366 backend tests pass (+6 new), ruff clean.**

**Commit:** `PENDING`

---

## Phase 4 — Surface the detail in the UI

**Objective:** the reviewer can *see and correct* the new fields on the Review
card, and an accepted leg shows its number/seat/airports in `LegForm`'s folded
"usually filled in from your email" section.

**Assumptions to validate first:**
- [ ] `LegForm.tsx` already has the folded fields for number/seat/IATA (README
      §1 says it does) — this is wiring, not new form design.
- [ ] The Review card renders from the serialised extraction payload
      (`_serialise` in `api/review.py`).

**Common problems to prepare for:**
- `frontend/src/types.ts` must gain the new fields or TypeScript drops them
      silently.
- Month-first dates and `parseDate` (never `new Date("...")`) for any new date
      rendering (README §6).

**Tasks:**
- [ ] Extend the extraction serialisation + `types.ts` with the new fields.
- [ ] Show number / origin→destination / times / seat on the Review card.
- [ ] Populate `LegForm`'s folded fields from accepted data.

**Tests that must pass to proceed:**
- [ ] `Review.test.tsx`: a full-detail proposal renders its flight number and
      route.
- [ ] `npm test` and `npm run build` green.

**Commit:** _(hash TBD)_

---

## Phase 5 — Deploy and verify on the real email

**Objective:** prove it end-to-end on the actual Pegasus booking.

**Tasks:**
- [ ] User pushes; run `deploy/deploy.sh` over SSH; confirm healthy.
- [ ] Verify the shipped bundle contains the change (README §4 asset grep).
- [ ] Re-extract the Pegasus email and confirm the Leg shows `PC1162, PC228`,
      `STN → NQZ`, both datetimes, and seats — measured against the DB, not just
      eyeballed.
- [ ] Update README §5 with the widened field set; note remaining out-of-scope
      items (cost/currency/hotel address).

**Commit:** _(hash TBD)_

---

## Lessons learned (fill in as phases complete)

- _(Phase 1)_ The change was pure additive plumbing: the DB columns and even
  `Booking.payload()` (which copies `__dict__`) already accommodated new fields,
  so widening was low-risk. Key design point that made it clean — **two tiers of
  cleaner**: `_clean_date` *raises* (a bad check-in date rejects the booking,
  because dates are load-bearing), while the new `_clean_iata` / `_clean_datetime`
  / `_clean_str_tuple` *never raise* (a bad seat or IATA nulls only itself). This
  is the "not missed" principle in code. `flight_numbers` is a tuple on the
  dataclass (frozen-safe) and a JSON array on the wire. Times are stored naive:
  `_clean_datetime` strips any timezone offset to keep the printed wall-clock.
- _(Phase 2)_ The override path has **two gates**, not one: `AcceptPayload`
  (pydantic model in `api/review.py`) drops any field it doesn't declare *before*
  `ALLOWED_OVERRIDES` (in `services/review.py`) is ever consulted — both must
  list a field for a reviewer to correct it. `_serialise` spreads `{**payload}`,
  so newly-extracted proposals already expose the detail over the API for free
  (a head start for Phase 4). `_at()` handles the timed-vs-date fallback with no
  new code (`_at(depart_at) or _at(start_date)`). `to_place` falls back to `city`
  so the arrival place never lands empty when only the city was read.
- _(Phase 3)_ The seam is a `fetch_body(email) -> str | None` callable injected
  into `process_email`/`run_extractions`, so the suite stays socket-free (tests
  pass a lambda). The real one, `imap_body_fetcher()`, is a context manager that
  opens **one** IMAP login for the whole batch and, crucially, must **degrade
  never fail**: it yields a None-returning fetcher if the mailbox won't open, and
  swallows a per-message re-fetch error to None. Subtlety hit while writing it —
  a `@contextmanager` must `yield` exactly once, so the "can we open?" try/except
  wraps only `from_settings()/__enter__`, never the `yield` itself (else a
  consumer exception would trigger a second yield). The manual path
  (`api/review.py`) still does its own inline re-fetch; it could later share this
  helper but was left as-is.
