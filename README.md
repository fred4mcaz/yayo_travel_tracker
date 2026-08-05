# Yayo travel

A self-hosted personal travel tracker, live at
[travel.foryayo.com](https://travel.foryayo.com). It keeps past, ongoing, and
upcoming international travel in one place — with a calendar and a map — watches
Gmail for booking confirmations and proposes entries for review, and flags what
you've forgotten to book.

Single user (Eduardo, `req4233@gmail.com`), single SQLite file, passkey-only
auth, no third-party runtime dependencies in the browser. Private repo,
self-hosted on a Hetzner box.

This is the one document to read before touching the code. It covers the domain
model, the architecture, how to run and deploy, the traps already hit, and
what's still open.

---

## 1. The domain model — read this before changing anything

This took six rounds of correction to land on. Do not re-derive it; do not
"improve" it without asking.

> **A trip is one international stay in one country.**
> One country. One passport. One arrival journey. Many hotels.

- Going to Vietnam then Thailand then Japan is **three trips**, not one. "New
  trip" is how a new country gets recorded — that is the entire point of that
  button.
- **Return travel is not tracked.** He does not record flights home. Every `Leg`
  is an arrival. There is no "no return leg booked" check and there should not
  be one.
- **Trips are never named by hand.** The label is derived from the hotels:
  `Hanoi · Sofitel Legend` for one stop, `Hanoi → Hue → Hoi An` for several,
  truncated past three. `Trip.title` was deleted.
- **The forms deliberately ask for very little**: country, city, dates, notes.
  Hotel name, confirmation code, carrier, flight number, seat and IATA codes sit
  behind a folded "usually filled in from your email" section. Address, cost and
  currency are not in any form at all — those columns exist only for the Gmail
  extractor to fill.

This invariant is **enforced in the API**, not just the UI: adding a second
country to a trip returns 409 naming both countries. See `_guard_single_country`
in `backend/app/api/trips.py`.

Dates render **month-first everywhere** — "Aug 1", "Aug 18–24, 2026" (see
`frontend/src/lib/format.ts`). The calendar week runs **Sunday to Saturday**.

**The Trips list is grouped, not one flat list**: Ongoing, Upcoming, No dates
yet, Past (see `GROUPS` in `views/Trips.tsx`). `GET /api/trips` sorts every trip
start-date descending, which is what Ongoing and Past want (most recent first).
**Upcoming is the exception** — it reads next-trip-first, so `Trips.tsx` flips
just that group to ascending: the soonest departure on top, each later one
below. Undated trips sort last.

**A calendar bar is one hotel booking; the box around it is the country stay.**
Bars used to be per-trip, which read like a booking but wasn't one. Each hotel
now gets its own colour and its own check-in → check-out, labelled
`City · Hotel`, drawn inside an outlined wrapper spanning the whole country
stay. That wrapper is the point: **any stretch of it with no bar over it is a
night in that country with nowhere to sleep** — the missing-hotel feature,
visible without opening the trip. A dated trip with no hotel at all is still a
wrapper, so nothing disappears from the grid. `layoutWeek` therefore returns
*groups*, not bars, and each group reserves its whole block of rows at once;
reserving only the top row let the next trip's bars land inside the previous
trip's box.

**Two consecutive stays share a row, with a travel hop in the gap.** When one
country stay ends the very day the next begins they have no night in common, so
they sit side by side on one row rather than stacking; the wrappers are shaved
apart at the boundary and a small mode glyph (✈ 🚆 🚌 ⛴ 🚗) floats in the gap,
showing how you travelled *into the later country* — its arrival leg. A faint
arrow stands in when that journey isn't recorded, the calendar's echo of the
missing-travel banner on the trip itself. The glyph is a marker only; it never
eats a click meant for a wrapper. The hop is drawn only for a pair on the **same
row in the same week** — cross-week and cross-lane pairs are left unconnected
(see §7).

**Dragging across calendar days creates a trip.** The dragged span maps
straight to check-in/check-out (`rangeFromDrag` in `lib/calendarRange.ts`), so
the bar you get back covers the days you swept; a single click is one night,
the minimum a stay can be. It creates the trip immediately and opens the stay
form, exactly as the "New trip" button does — cancelling leaves an empty undated
trip either way. Mouse only for now: touch drag-select needs `elementFromPoint`
tracking and was left unbuilt.

### Missing-hotel detection (the feature he cares most about)

Flags any stretch inside a country stay with no hotel booked: between two
hotels, between landing and the first booking, and after the last booking.
Shown amber inside the country block with a button straight to the form, and
counted on the trip card.

**The "Leaving Country On" date is load-bearing.** It is named for the country
on purpose — the one thing it must never be mistaken for is a hotel checkout.
Without it the stay can only end at
the last checkout, so "two weeks in Vietnam, first four nights booked" — the
most common way to forget a hotel — looks complete. It is optional; if unset,
only gaps *between* bookings show. It also extends the trip's date span.

Deliberately silent about: overlapping bookings (double-booked is a different
problem) and anything after the last hotel when no leaving date is set.

### Missing-travel detection

The sibling of missing-hotel: every `Leg` is an arrival, so a trip with a
country but no leg records **nothing about how you got there** — usually a
flight not yet booked. A future or ongoing trip in that state shows a warn
banner at the top of its detail panel, with a shortcut into the "How you get
there" form. **Past trips stay quiet** — old flights routinely go
un-backfilled, so a banner there is noise, not signal. Undated trips are silent
too. The same emptiness shows on the calendar as the faint-arrow hop between
this trip and the previous one.

### Merging two trips into one

The flip side of strict matching: when a later hotel lands outside a trip's
dates it becomes its own trip, but sometimes it was really the same stay all
along. **Merge** folds one trip's hotels, journeys, notes, requirements and
country entry into another and deletes it — then the gap between them shows as
unbooked nights, which is the whole point. It is always a deliberate act: the
detail panel *offers* a merge for any same-country trip whose dates are
overlapping or within `MERGE_ADJACENCY_DAYS` (30), and you confirm it. Refused
across countries — a trip is one country — with a 409, exactly like adding a
second country. `merge_trips` and `mergeable_trips` live in `services/trips.py`;
the route is `POST /api/trips/{id}/merge`.

**Keep separate** is the persistent opposite of a merge. The suggestion is
recomputed on every load, so a suggestion you reject would otherwise come back;
"Keep separate" records the pair in the `merge_dismissal` table and
`mergeable_trips` skips it from then on. It is stored as an unordered pair, so
dismissing from either trip's panel silences both, and either trip being
deleted — including when one is absorbed by a merge — cascades the row away.
`keep_trips_separate` lives in `services/trips.py`; the route is
`POST /api/trips/{id}/keep-separate`. There is deliberately no "un-dismiss" in
the UI — keeping trips apart is the default, and if a pair really is one stay
you merge them.

### Immigration readiness

Answers, per trip: are you eligible to enter on the passport you're carrying,
do you need a visa (or e-visa / visa-on-arrival / ETA), and do you need to
submit an arrival card? Shown as a compact badge (✅ ready / ⚠️ action /
❔ unknown) on the trip card and a full "Immigration readiness" section on the
trip detail, and confirmed via Gmail like everything else in §5.

**The policy source is the LLM, at runtime, cached forever.**
`services/entry_policy.py#get_policy` asks a strict tool "what does a
US/MX passport holder need to enter country X" once per `(country_code,
nationality)` and caches the answer in `EntryPolicy` — there is **no refresh
path at all**, by design; border rules change without notice, and every
reading shows a "checked \<date\>" line so the staleness risk stays visible
rather than hidden. An unconfigured box (`YAYO_OPENROUTER_API_KEY` unset)
reads `unknown`, never errors.

**Readiness is computed for the trip's selected passport, defaulting to
US.** Selecting the MX passport on a `CountryEntry` recomputes the whole
reading for that trip. `services/trips.py#sync_requirements` turns a cached
policy into the trip's actual `Requirement` checklist rows (`source=system`),
reconciling them on every hotel/leg/passport change — but it **never touches**
a row a human added by hand or an email confirmed (`source=manual`/`email`),
even if the policy would no longer require it. `trip_readiness` derives the
compact/full reading purely from what's cached and what rows already exist;
it never triggers a fetch itself.

**Confirmed via Gmail, two ways**, both landing as Review-queue proposals —
nothing writes to a `Requirement` without an explicit accept, same boundary as
§5:
- **Automatic, local, no LLM** (`services/immigration.py#propose_confirmation`):
  a *second*, disjoint sender/keyword allow-list
  (`immigration_sender_domains`/`immigration_keywords` in
  `email-filter.json`) flags government mail
  (`EmailMessage.looks_like_immigration`) entirely on-box. A bare sender+date
  match against a trip's outstanding `entry_card` requirement is as far as
  this goes — never sent to a model.
- **Manual, model-read** (`extract_selected_immigration`): picking an
  *unflagged* email from the recent-emails list (or a flagged one, for a
  richer read) is the per-message consent to extract it — reads a real
  requirement kind, a reference, and a **nationality** out of the body.

**The arrival card and onward ticket are automated read-only indicators — no
dropdown.** The *arrival card* reads one of three states, driven entirely by the
Gmail pipeline above: **none** (no confirmation email has matched this trip),
**received** (a confirmation matched and is waiting in the Review queue — the
receipt is surfaced the moment mail lands, but it only *counts* once accepted,
the boundary holds), or **confirmed** (the `entry_card` requirement was
accepted). The *onward ticket* is derived live from booked journeys: it's
confirmed when a `Leg` departs the trip's country near its end date — which is
exactly how an onward/return flight is already recorded, since every `Leg` is an
*arrival into* a country and the journey out of trip X is a later trip's inbound
leg. Both show only when the policy requires them, and neither is hand-settable
(`services/trips.py#_arrival_card_reading` / `_onward_ticket_reading`); the other
requirement kinds (visa, ETA, insurance, vaccination) keep their status
dropdowns.

**The loud discrepancy flag.** If a Phase-5 read names a nationality that
differs from the trip's *currently* selected passport, `Requirement`'s
`discrepancy_nationality` is stamped on accept — the raw fact, not a stored
verdict. `trip_readiness` compares it against the live passport selection on
every read, so a red "Passport mismatch" banner appears on the card and the
detail panel, and **clears itself** the moment the passport is corrected —
the only thing that still renders on an otherwise-quiet past trip.

---

## 2. Architecture and layout

| Path | What's in it |
|---|---|
| `backend/` | FastAPI app, SQLModel schema, Alembic migrations, background jobs |
| `frontend/` | React + Vite SPA, built to static assets and served by the backend |
| `data/` | Committed static reference assets: world GeoJSON, city coordinates, country rules |
| `var/` | **Runtime state — gitignored.** SQLite database, backups, stored email |
| `deploy/` | Dockerfile, compose file, Caddy snippet, deploy script |

`data/` is committed and read-only; `var/` is everything mutable — copy it and
you have the whole app's state.

Inside the two apps:

```
backend/app/
  models.py            17 tables. Times are naive local wall-clock, by design
  schemas.py           Create/Update payloads, separate from tables
  countries.py         GENERATED — run scripts/build_countries.py
  api/trips.py         Trips, hotels, travel, paperwork, passport-used,
                       immigration readiness (compact on GET /api/trips, full
                       on trip detail — see §1). GET /api/trips carries a
                       compact stay per hotel, because the calendar draws a
                       bar for each and only ever sees the list payload
  api/{auth,passports,notes,geo,review,export}.py
  services/trips.py    All derived state: label, status, country, unbooked
                       gaps, sync_requirements/trip_readiness (§1)
  services/entry_policy.py  The cached, never-refreshed LLM policy lookup (§1)
  services/auth.py     WebAuthn, sessions, recovery codes — all hashed
  services/geocode.py  City → lat/lon and autocomplete, from bundled data
  services/            email_ingest, email_filter, extraction, immigration,
                       review, scheduler (see §5)
frontend/src/
  views/TripDetail.tsx The country-first detail panel
  views/{Trips,Calendar,Map,Settings,Auth,Review}.tsx
  components/          CityInput, CountrySelect, DateField, StayForm, LegForm, Sheet
  lib/countries.ts     GENERATED — source of truth for country names
  lib/format.ts        Date formatting and parsing (month-first; parseDate)
  lib/calendarRange.ts Maps a calendar drag to stay dates (see §1)
  lib/immigration.ts   Readiness badge + discrepancy copy, shared by the card
                       and the detail panel so the two never word it differently
  **/*.test.{ts,tsx}   vitest, colocated with what they cover — `npm test`
data/geo/              GENERATED — run scripts/build_geo.py
```

### Feature surface

| Area | State |
|---|---|
| Auth | Passkeys only, bound to the hostname. Recovery codes hashed |
| Data model | 17 tables, 9 Alembic migrations, run on container start |
| Trip entry | Country picker, city autocomplete, date steppers — works end to end |
| Trip detail | Country-first panel, capped to 70% width and left-justified (`.pane-detail`) |
| Missing hotels | See §1. "Leaving Country On" sits at the bottom of the country block, and the uncovered part of a calendar wrapper says the same thing visually |
| Missing travel | Warn banner on the trip detail when a future/ongoing trip has a country but no arrival leg, with a shortcut to the leg form. Silent on past and undated trips. See §1 |
| Trips list | Grouped Ongoing / Upcoming / No dates yet / Past; Upcoming ordered soonest-first, the rest most-recent-first. See §1 |
| Merge trips | See §1. Detail panel offers a merge for a same-country, near-dated trip; folds it in and deletes it. Refused across countries. "Keep separate" persistently dismisses a suggestion (`merge_dismissal` table) |
| Calendar | Sunday-to-Saturday month grid; one distinctly-coloured bar per hotel, offset to start mid-check-in-day and end mid-checkout-day, inside an outlined wrapper for the country stay; notes as dots. Consecutive stays share a row with a travel-mode hop in the gap (§1). Drag across days to start a new trip with those dates pre-filled |
| Map | Canvas world map, country fill, city pins, route arcs. No tile server |
| Passports | Two passports (MX, US), last-4 only |
| Immigration readiness | Live — per-trip visa/arrival-card/ETA status from a cached LLM policy lookup, confirmed via Gmail, with a loud passport-mismatch flag. See §1 and §5 |
| Gmail ingest | Live and on — fetch → filter → extract → propose → you accept. §5 |
| Export | Live — Settings → Export: full JSON, or a CSV zip (trips/hotels/legs), ASCII-folded |
| Backup | Pre-deploy SQLite snapshot only. No schedule, no off-box copy |

### Static reference data (`data/`)

Committed, read-only data baked into the container image. Anything mutable lives
in `var/` instead. These directories must contain at least one tracked file
(see the empty-directory trap in §6).

| Path | Contents |
|---|---|
| `geo/countries.min.geojson` | Natural Earth 110m admin-0 boundaries, simplified. Drives the country fills on the map |
| `geo/cities.min.json` | GeoNames subset (population > 15k) giving lat/lon per city, so typing a city name places a pin with no geocoding API |
| `rules/email-filter.json` | The Gmail allow-lists and keyword rules (§5): the booking allow-list, **and** a *disjoint* immigration allow-list (`immigration_sender_domains` → country, `immigration_keywords`) for government/arrival-card mail (§1, §5). The only file in `rules/` |

The `entry-requirements.json` / `visa-free.json` static datasets that earlier
drafts of §7 anticipated were **never built and are no longer planned** — the
immigration-readiness feature (§1) answers the same question at runtime via a
cached LLM lookup (`EntryPolicy`) instead of a shipped table, precisely because
border rules change without notice and a committed file would silently rot.

---

## 3. Local development

Backend, from `backend/` (no env vars needed; paths resolve to the repo root):

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python.exe ../scripts/dev_backend.py
```

That wrapper pulls a fresh snapshot of the **live production database** into
`var/travel.db` and then starts uvicorn exactly as before, so local review shows
the same data as [travel.foryayo.com](https://travel.foryayo.com). The pull runs
once per launch (uvicorn owns the `--reload` loop), is read-only against the box,
and is non-fatal — offline, it keeps whatever local copy exists and still starts.
Because every start overwrites the local DB, **local edits are throwaway**; only
the online instance is official, which is the intended behaviour. To refresh
without restarting, run `scripts/sync_prod_db.py` on its own. To run against a
local DB instead — an isolated scratch DB for destructive testing, or offline —
start uvicorn directly and skip the wrapper:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Frontend, from `frontend/`:

```bash
npm install
```

```bash
npm run dev
```

`npm run dev` serves on :5173 and proxies `/api` to :8000. Alternatively run
`npm run build` once and the backend serves the built SPA from :8000 directly.

Tests, ruff, and typecheck (kept clean):

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
```

The frontend has a small vitest suite (jsdom, dev-only deps), from `frontend/`:

```bash
npm test
```

### Verifying UI changes in a browser

Every input bug in this project was found by driving the real UI, never by
reading code. Local verification needs no passkey:

- **The passkey wall is down in local dev.** `Settings.auth_optional` is
  `not is_production`, and `is_production` is `site_origin.startswith("https://")`
  — so on `http://localhost` `require_auth` waves everything through and
  `/api/auth/status` reports `authenticated: true`, and the frontend skips the
  login screen. The Hetzner box serves over https, so the wall stands there by
  construction; there is no flag to forget. The app logs a loud `AUTH BYPASS
  ACTIVE` banner at startup whenever it is on, so it can never be mistaken for
  the deployed instance. This changes nothing about where data lives: the local
  DB is a throwaway dev copy either way — only the online instance is official
  and saved. (The old trick — minting a session with `create_session` and
  setting `document.cookie = "yayo_session=<token>; path=/"` — still works if you
  ever want to test the *real* logged-in path, but you no longer need it.)
- **The local DB is a disposable mirror of production.** `dev_backend.py`
  overwrites `var/travel.db` from the live box on every start (see above), so it
  is safe to mutate while testing — the next launch refreshes it, and nothing you
  do locally ever reaches the online data. When you instead need isolation from
  the real data — destructive testing you don't want re-pulled, or working
  offline — point `YAYO_VAR_DIR` at a scratch dir via a gitignored `backend/.env`,
  run migrations + a seed script there, and start uvicorn directly (not the
  wrapper).
- **Trust `getBoundingClientRect` and `input.value` over eyeballing a
  screenshot** — screenshots letterbox, and the a11y tree reports an input's
  *placeholder* as its accessible name. Two "bugs" were misread that way. The
  preview pane also cannot always composite frames (screenshots then time out),
  so measuring is sometimes the *only* option — it is the better one regardless.
  The calendar wrapper's missing bottom border was found exactly this way: two
  rects reporting `bottom: 297`.
- **Never click and then read the DOM in the same injected script.** React 18
  batches state updates, so the DOM you read back is the *pre-click* one. A
  click-then-assert loop reported a sheet as still open through five tab
  switches when it had actually closed. Do one action per injected call and read
  state in the next call. Anything async (a `POST` before the UI updates) needs a
  real wait, not just a second call.
- **Dispatching `mouseenter` by hand does nothing.** React derives
  `onMouseEnter` from delegated `mouseover`, so a synthetic `mouseenter` alone
  is dropped and a drag silently collapses to a single cell. Fire `mouseover`
  too. This looked exactly like a broken date range.

---

## 4. Deploying

**The user pushes, you pull.** You cannot `git push` — the permission
classifier blocks it, so ask him to push. He runs `git push`; you then run the
deploy script over SSH:

```bash
ssh yayokun@5.78.184.240 'cd /srv/yayo_travel_tracker && ./deploy/deploy.sh'
```

The script pulls, snapshots the database with `sqlite3 .backup` before touching
anything, rebuilds the image, restarts, and polls `/api/health`. It exits
non-zero and prints recent logs if the app does not come up, so a broken deploy
is loud rather than silent. Healthy in ~4s on a clean build.

> **Do not skip the deploy.** Three commits were once left undeployed across two
> turns because "push and I'll deploy" was said and never done. He reported the
> features as missing and was right. After he pushes, deploy, then verify the
> shipped bundle actually contains the change:
>
> ```bash
> ssh yayokun@5.78.184.240 'ASSET=$(curl -sS https://travel.foryayo.com/ | grep -o "/assets/index-[A-Za-z0-9_-]*\.js"); curl -sS "https://travel.foryayo.com$ASSET" | grep -c "some new string"'
> ```

### Checking on it

```bash
ssh yayokun@5.78.184.240 'docker compose -f /srv/yayo_travel_tracker/deploy/docker-compose.yml logs -f app'
```

```bash
ssh yayokun@5.78.184.240 'curl -s http://127.0.0.1:8081/api/health'
```

### Rolling back

Images are rebuilt from the checkout, so a rollback is a checkout plus a
redeploy. On the box: `git checkout <previous-good-sha>` then
`docker compose -f deploy/docker-compose.yml up -d --build`. If a migration is
at fault, restore the pre-deploy snapshot from `var/backups/`.

### The server

- `5.78.184.240`, user `yayokun`, Ubuntu 26.04, **1.9 GB RAM / 4 GB swap**
  (the frontend builds fine on that box; that was checked)
- Checkout at `/srv/yayo_travel_tracker`, owned by `yayokun`
- Docker 29.1.3 + compose v2 (`docker-compose-v2`, not `docker-compose-plugin`)
- **Caddy on the host** serves `travel.foryayo.com` → `127.0.0.1:8081`. It also
  serves other sites; never restart it carelessly, and back up
  `/etc/caddy/Caddyfile` before editing
- Private repo, cloned via a read-only deploy key at `~/.ssh/yayo_travel_deploy`
  under the host alias `github-yayo-travel`. Read-only is deliberate: the server
  only pulls, so a leaked key cannot rewrite history
- **`sudo` needs his password — you cannot use it.** Anything requiring root
  must be handed to him as **one command per fenced block** (a multi-line paste
  once fed a line into a sudo password prompt)

### One-time setup (for a rebuild from scratch)

1. **DNS** — an `A` record for `travel.foryayo.com` → `5.78.184.240`. Verify
   with `dig +short travel.foryayo.com` before going further; Caddy cannot issue
   a certificate until it resolves, and passkeys are bound to the hostname.
2. **Clone** — `git clone github-yayo-travel:fred4mcaz/yayo_travel_tracker.git /srv/yayo_travel_tracker`.
3. **Env file** — `cp deploy/.env.example deploy/.env`, then edit it and
   `chmod 600` it. `YAYO_RP_ID` must be exactly `travel.foryayo.com` — passkeys
   are cryptographically bound to it and changing it later invalidates every
   registered passkey.
4. **Caddy** — append `deploy/Caddyfile.snippet` to `/etc/caddy/Caddyfile`,
   `caddy validate`, then `systemctl reload caddy`. Caddy requests the
   certificate on the first inbound request.
5. **First deploy** — `./deploy/deploy.sh`.

---

## 5. Gmail ingest — live and on

The reason the forms ask for so little: a booking email becomes a trip, with a
human accept in the middle. The whole thing is **off unless
`YAYO_EMAIL_INGEST_ENABLED=true`**, and it is currently on in `deploy/.env` with
the Gmail app password and OpenRouter key in place.

> **The line that must not move:** nothing writes to trip data without an
> explicit accept. Extractions land in `extraction` with `status=pending`;
> **accepting is the only code path** that may create or modify a `Trip`,
> `Stay`, `Leg`, or `CountryEntry`. Matching and dismissing write nothing. This
> is asserted directly as a row-count test, and a *failed* accept leaves no
> orphan trip.

**The cycle, every 10 minutes** (APScheduler, started from the app lifespan in
`services/scheduler.py`):

1. **Fetch** — `services/email_ingest.py`. IMAP over TLS, `UID > watermark`. The
   first run records the watermark and **ingests nothing** — no historical
   backfill, by his choice. Dedupes on Message-ID; survives the IMAP `n:*` range
   quirk (the newest message comes back even when nothing is above `n`) and a
   `UIDVALIDITY` reset. Stores only sender, subject and a 400-char snippet in
   `email_message` — never the full body.
2. **Filter** — `services/email_filter.py`, rules in
   `data/rules/email-filter.json`. **The privacy boundary.** Only mail from an
   allow-listed booking sender, carrying a confirmation keyword and no marketing
   keyword, is marked `looks_like_travel`. Everything else stays on the box and
   never reaches the API. Domain matching requires the dot (`booking.com`
   does not match `notbooking.com`); denials are subject-only, keywords match
   subject or body. Extend the allow-list as new senders show up.
   A **second, disjoint** classifier (`classify_immigration`) runs here too,
   against the immigration allow-list, and sets `looks_like_immigration` — a
   sibling flag, never unioned with `looks_like_travel`. Government mail never
   rides in on the booking rules and vice versa. Setting this flag sends
   nothing anywhere; it only feeds the immigration path below.
3. **Extract** — `services/extraction.py`. Candidates go through **OpenRouter's
   OpenAI-compatible API** (`openai` SDK pointed at openrouter.ai) behind
   **strict** function-calling tools: Claude Haiku triages ("is this really a
   booking?"), and only on a yes does Claude Sonnet pull out the structured
   booking. Model slugs are config (`YAYO_TRIAGE_MODEL` / `YAYO_EXTRACT_MODEL`,
   defaulting to `anthropic/claude-haiku-4.5` and `anthropic/claude-sonnet-5`).
   `validate_booking` re-checks every field regardless of whether the provider
   enforced strict, so a malformed response persists nothing. The model is
   injected behind a Protocol, so the whole pipeline tests offline.
   **The year anchor:** the extractor is handed the email's *received date* as a
   system message — a booking is for a stay on or after it — because the model
   has no other way to know the year and otherwise defaults to a past one (it
   once read Aug 2026 as Aug 2025). A second, deterministic guard (`correct_year`)
   rolls a deep-past, cross-year check-in forward, shifting checkout to keep the
   stay's length; near and same-year dates are left untouched. We never backfill
   old mail, so a check-in a year before the email is always a misread year.
4. **Match** — `services/review.py`. `find_matching_trip` attaches a booking to
   an existing trip by same-country **and** ±2-day date overlap, otherwise
   proposes a new trip. A different-country booking becomes its own trip **by
   construction** — the one-country rule holds without ever failing the guard.
   **Matching stays deliberately strict:** a hotel whose dates fall outside a
   trip's span (common when no leaving date is set, so the span ends at the last
   checkout) becomes *its own* trip rather than being force-fit. Recombining two
   trips that are really one stay is a manual **merge** (see §1), not a guess the
   matcher makes.
5. **Review** — `api/review.py`, `views/Review.tsx`. Proposals appear in the
   **Review tab** with a badge count. Accept or dismiss; correct a field the
   model missed first (overrides are allow-listed to booking fields). Accepting
   goes through the same derived-state path a manual edit does, so an accepted
   booking and a typed one cannot diverge.
   **Dismiss is one-way, by design.** It flips the extraction to `rejected` and
   nothing else — the email keeps its `processed_at` stamp, so a later poll never
   re-reads it and it does not come back. Re-reading a batch is therefore a
   deliberate act, done with a one-time data migration that deletes the
   non-accepted proposals and clears `processed_at` on their emails (accepted
   trips untouched, IMAP watermark untouched). Migration `f2a7c9d34b58` did
   exactly this once, to re-read pre-year-anchor mail; copy it if you ever need
   another clean slate. After it runs the Review tab is empty until the next poll
   re-extracts.

**The gate.** With the flag off (the default and shipped-off state) nothing
starts, connects, or reads a credential. On but a credential missing: the
container **fails to boot loudly**, naming the unset vars (never their values).
`POST /api/review/poll` (the "Check email now" button) runs one cycle on demand,
gated identically — 409 with the reason if off or unconfigured. Costs a little
when on: Haiku per triaged candidate, Sonnet only when triage says yes.

### The immigration path (a sibling of the booking cycle)

Immigration confirmations ride the same fetch/filter/review machinery but split
off at the filter (`looks_like_immigration`, above) and are handled by
`services/immigration.py`. An `Extraction.kind` column (`booking` |
`immigration`) is the discriminator; both kinds share the Review tab and the
one accept boundary.

- **Automatic, on-box, no LLM.** `run_immigration_matching` runs every poll
  (needs no OpenRouter key). It matches a flagged email to a trip's outstanding
  `entry_card` requirement by date and — when the sender domain maps to one —
  country, and proposes a confirmation. Ambiguous (two qualifying trips) means
  no proposal; a human decides.
- **Manual, model-read.** From the recent-emails list, "As immigration doc"
  (`?kind=immigration`) sends the picked email to a strict
  `record_immigration_document` tool that reads a requirement kind, a reference,
  and a nationality — the per-message consent, same D2 rule as the booking
  manual-extract.
- **Accept flips one `Requirement` to `approved`** (`source=email`, so
  `sync_requirements` never later clobbers it), pre-filling the reference from
  the reading unless the reviewer typed one. A read nationality is stamped onto
  `discrepancy_nationality`; the loud mismatch flag is computed live at read
  time (§1), never stored as a verdict.

---

## 6. Traps already hit — do not rediscover these

**Windows dev machine, Linux server.**
- `.gitattributes` pins `eol=lf` for shell scripts, Dockerfile and YAML. Without
  it, `deploy.sh` checks out CRLF and Linux fails with a misleading "not found".
- Git on Windows does not set the executable bit. `deploy.sh` and
  `entrypoint.sh` are mode `100755` in the index via `git update-index --chmod=+x`.
- Git does not track empty directories. `data/geo` and `data/rules` hold
  `.gitkeep` files; deleting them breaks `COPY data/` in the image build with a
  bare `"/data": not found`.
- `.dockerignore` exists because the build otherwise copied `backend/.venv` and
  `node_modules` into the image.

**Runtime.**
- `deploy.sh` git-pulls itself. Its whole body is inside `main()` so bash parses
  the file before executing; without that it resumes at a stale byte offset and
  silently skips commands.
- A bind mount keeps the *host* directory's ownership. The container runs as the
  host uid (`YAYO_UID`) so it can write `var/`.
- SQLite cannot add a `NOT NULL` column without `server_default`.
- The **CSV export ASCII-folds every cell** (`_ascii` in `api/export.py`): the
  `city · hotel` label separator is a UTF-8 middle dot that a Windows-codepage
  spreadsheet renders as `Â·`. Folding to a plain dash — and stripping accents —
  is deliberate; do not "fix" it back to raw UTF-8. The **JSON export stays
  full-fidelity**; only the spreadsheet is flattened.

**React / frontend.**
- `Sheet`'s effect must not depend on `onClose` — callers pass inline arrows, so
  it re-ran every keystroke and its autofocus stole the caret. That was the
  "city field only accepts one character" bug.
- `DateField` steps from a **ref**, not the prop: React batches, so rapid stepper
  clicks all read the same stale value and seven clicks moved one day.
- Never `new Date("2026-03-18")` — that is UTC midnight and renders as the 17th
  west of Greenwich. Use `parseDate` in `lib/format.ts`.
- Inputs are 16px because iOS Safari zooms on focus below that.
- **`openStayOnMount` is read by a `useState` initializer, so it only fires on a
  real mount** — and that cuts both ways. Both halves shipped as bugs:
  - The detail panel renders inside `tab === "trips" &&`, so leaving the Trips
    tab unmounts it. A flag left set therefore re-fired on *every* return, and
    "Add a country" popped open each time you clicked Trips. The flag is a
    one-shot: the panel calls `onStayOpened` once it has acted, and `App` clears
    it there. The open sheet survives because `editing` is state, not a prop.
  - Conversely, selecting a *different* trip does not remount the panel — React
    reuses the instance and only swaps props — so the initializer never re-ran
    and the form silently never appeared when a trip was already open. Hence
    `key={selected.id}` on `TripDetailPanel`.
- A field named `date` on a SQLModel class shadows the `date` type and pydantic
  cannot resolve it. `Note.on_date` is named that for this reason.

---

## 7. What's left

- **Notes creation UI.** The notes API is complete and notes render on the
  calendar and trip detail, but there is still no way to create one from the UI.
- **Calendar drag is mouse-only.** No touch equivalent, so it does nothing on a
  phone — where the calendar is most used. Needs `touchmove` +
  `document.elementFromPoint`, since touch events stay targeted at the element
  the gesture started on and never fire `mouseover` on the ones it crosses.
- **Travel hops connect only same-row, same-week pairs.** The mode glyph between
  two consecutive trips (§1) is drawn only when both wrappers share a row within
  one week and sit within `CONNECTOR_MAX_GAP` (1) days. A hop across a week
  boundary, or between trips the layout stacked onto different rows, is left
  undrawn — the horizontal-gap anchor has nowhere to go in those cases.
- **The frontend suite covers the calendar (drag, week layout, hotel bars), the
  stay-form-on-mount lifecycle, the Trips-list ordering, the merge card's
  keep-separate flow, the readiness badge / discrepancy copy (incl. the
  automated arrival-card `state` and onward-ticket notes), and the Review
  queue's two card kinds** (56 tests). Everything else in the SPA is still
  untested; there is no `App`-level test, because that needs the `api` module
  mocked (though `TripDetail.test.tsx` and `Review.test.tsx` mock individual
  `api` calls, which is the pattern an `App`-level test would extend).
- **Backup is still thin.** Only the pre-deploy snapshot in `deploy.sh` exists:
  same disk, no schedule, no off-box copy, and `YAYO_BACKUP_KEEP_DAYS` is
  defined but unwired (nothing prunes). He was offered scheduled/off-box backups
  and declined for now — export was the piece he wanted.
- **Nightly backup cron and the ICS feed** are unbuilt. The `ics` dependency is
  already pinned in `requirements.txt`, ready for the feed.
- **Immigration readiness open edges.** Visa/ETA confirmations only get an
  automatic (LLM-free) proposal for `entry_card`; other kinds need the manual
  "As immigration doc" path. `trip_readiness` reads from what's *already*
  cached and doesn't trigger a fetch, so a policy first cached by one trip only
  reaches a sibling same-country trip after that sibling is next mutated. A
  wrong cached policy can only be corrected by a manual DB edit — there is no
  refresh path, by design (§1). The onward-ticket check is a proxy: with no
  airport→country lookup, "departs this country near the trip's end" is
  approximated as "a booked `Leg` arriving a *different* country within a few
  days of the end date," so a leg from an unrelated trip could in principle
  count — acceptable, since it still evidences onward travel around that time.

Smaller things he was once offered: passport-expiry warnings against entry
dates, scheduled/off-box backups, an ICS subscribe feed.

---

## 8. How he works

- **He iterates by using it and reporting friction.** Expect the model to keep
  moving. When he corrects the design, take the correction literally and delete
  what it displaces — he explicitly said not to keep backwards compatibility.
- **Verify in a browser, do not trust the code** (§3). Every input bug in this
  project was found by driving the real UI.
- This is still a **development environment**. He has said to delete freely.
  That will change when it becomes official — ask before assuming it still holds.
