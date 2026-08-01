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
`frontend/src/lib/format.ts`). Calendar bars are **per-trip by explicit choice**
(a trip is one country stay); do not switch them to per-hotel without asking.

### Missing-hotel detection (the feature he cares most about)

Flags any stretch inside a country stay with no hotel booked: between two
hotels, between landing and the first booking, and after the last booking.
Shown amber inside the country block with a button straight to the form, and
counted on the trip card.

**The "Leaving on" date is load-bearing.** Without it the stay can only end at
the last checkout, so "two weeks in Vietnam, first four nights booked" — the
most common way to forget a hotel — looks complete. It is optional; if unset,
only gaps *between* bookings show. It also extends the trip's date span.

Deliberately silent about: overlapping bookings (double-booked is a different
problem) and anything after the last hotel when no leaving date is set.

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
  models.py            14 tables. Times are naive local wall-clock, by design
  schemas.py           Create/Update payloads, separate from tables
  countries.py         GENERATED — run scripts/build_countries.py
  api/trips.py         Trips, hotels, travel, paperwork, passport-used
  api/{auth,passports,notes,geo,review,export}.py
  services/trips.py    All derived state: label, status, country, unbooked gaps
  services/auth.py     WebAuthn, sessions, recovery codes — all hashed
  services/geocode.py  City → lat/lon and autocomplete, from bundled data
  services/            email_ingest, email_filter, extraction, review, scheduler (see §5)
frontend/src/
  views/TripDetail.tsx The country-first detail panel
  views/{Trips,Calendar,Map,Settings,Auth,Review}.tsx
  components/          CityInput, CountrySelect, DateField, StayForm, LegForm, Sheet
  lib/countries.ts     GENERATED — source of truth for country names
  lib/format.ts        Date formatting and parsing (month-first; parseDate)
data/geo/              GENERATED — run scripts/build_geo.py
```

### Feature surface

| Area | State |
|---|---|
| Auth | Passkeys only, bound to the hostname. Recovery codes hashed |
| Data model | 14 tables, 3 Alembic migrations, run on container start |
| Trip entry | Country picker, city autocomplete, date steppers — works end to end |
| Trip detail | Country-first panel, capped to 70% width and left-justified (`.pane-detail`) |
| Missing hotels | See §1. "Leaving on" sits at the bottom of the country block |
| Calendar | Month grid; each trip a distinctly-coloured bar offset to start mid-arrival-day and end mid-checkout-day; notes as dots |
| Map | Canvas world map, country fill, city pins, route arcs. No tile server |
| Passports | Two passports (MX, US), last-4 only |
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
| `rules/email-filter.json` | The Gmail allow-list and keyword rules (§5). Currently the only file in `rules/` |
| `rules/entry-requirements.json` | *(designed, not built)* per-country paperwork: entry card, e-visa, ETA |
| `rules/visa-free.json` | *(designed, not built)* permitted visa-free days per passport. Advisory only — visa rules change without notice |

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

### Verifying UI changes in a browser

Every input bug in this project was found by driving the real UI, never by
reading code. To skip the passkey during local verification:

- Mint a session with `create_session` and set the cookie via JS:
  `document.cookie = "yayo_session=<token>; path=/"`. The `yayo_session` cookie
  is `httponly`, but that only blocks JS from *reading* it — a JS-set cookie of
  the same name is still sent, and the server reads by value.
- **Use a throwaway DB, never the real dev DB.** Point `YAYO_VAR_DIR` at a
  scratch dir via a gitignored `backend/.env`, run migrations + a seed script
  there, and remove it all afterwards.
- **Trust `getBoundingClientRect` and `input.value` over eyeballing a
  screenshot** — screenshots letterbox, and the a11y tree reports an input's
  *placeholder* as its accessible name. Two "bugs" were misread that way.

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
3. **Extract** — `services/extraction.py`. Candidates go through **OpenRouter's
   OpenAI-compatible API** (`openai` SDK pointed at openrouter.ai) behind
   **strict** function-calling tools: Claude Haiku triages ("is this really a
   booking?"), and only on a yes does Claude Sonnet pull out the structured
   booking. Model slugs are config (`YAYO_TRIAGE_MODEL` / `YAYO_EXTRACT_MODEL`,
   defaulting to `anthropic/claude-haiku-4.5` and `anthropic/claude-sonnet-5`).
   `validate_booking` re-checks every field regardless of whether the provider
   enforced strict, so a malformed response persists nothing. The model is
   injected behind a Protocol, so the whole pipeline tests offline.
4. **Match** — `services/review.py`. `find_matching_trip` attaches a booking to
   an existing trip by same-country **and** ±2-day date overlap, otherwise
   proposes a new trip. A different-country booking becomes its own trip **by
   construction** — the one-country rule holds without ever failing the guard.
5. **Review** — `api/review.py`, `views/Review.tsx`. Proposals appear in the
   **Review tab** with a badge count. Accept or dismiss; correct a field the
   model missed first (overrides are allow-listed to booking fields). Accepting
   goes through the same derived-state path a manual edit does, so an accepted
   booking and a typed one cannot diverge.

**The gate.** With the flag off (the default and shipped-off state) nothing
starts, connects, or reads a credential. On but a credential missing: the
container **fails to boot loudly**, naming the unset vars (never their values).
`POST /api/review/poll` (the "Check email now" button) runs one cycle on demand,
gated identically — 409 with the reason if off or unconfigured. Costs a little
when on: Haiku per triaged candidate, Sonnet only when triage says yes.

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
- A field named `date` on a SQLModel class shadows the `date` type and pydantic
  cannot resolve it. `Note.on_date` is named that for this reason.

---

## 7. What's left

- **Notes creation UI.** The notes API is complete and notes render on the
  calendar and trip detail, but there is still no way to create one from the UI.
- **`api/notes.py` bug.** `list_notes` reads `Trip.title`, which was deleted, so
  `GET /api/notes` 500s for any note that has a `trip_id`. No note has one yet,
  so it has not surfaced. Swap to the derived label (see `api/review.py`'s
  `_trip_label`).
- **`Requirement` rows** render read-only on trip detail; nothing creates them.
- **Backup is still thin.** Only the pre-deploy snapshot in `deploy.sh` exists:
  same disk, no schedule, no off-box copy, and `YAYO_BACKUP_KEEP_DAYS` is
  defined but unwired (nothing prunes). He was offered scheduled/off-box backups
  and declined for now — export was the piece he wanted.
- **Nightly backup cron and the ICS feed** are unbuilt. The `ics` dependency is
  already pinned in `requirements.txt`, ready for the feed.
- **Visa-free advisory dataset** (`data/rules/visa-free.json`,
  `entry-requirements.json`) was designed but never built. `email-filter.json`
  is the only thing in `data/rules/` so far.

Smaller things he was once offered: entry-card/visa reminders per country,
passport-expiry warnings against entry dates, scheduled/off-box backups, an ICS
subscribe feed.

---

## 8. How he works

- **He iterates by using it and reporting friction.** Expect the model to keep
  moving. When he corrects the design, take the correction literally and delete
  what it displaces — he explicitly said not to keep backwards compatibility.
- **Verify in a browser, do not trust the code** (§3). Every input bug in this
  project was found by driving the real UI.
- This is still a **development environment**. He has said to delete freely.
  That will change when it becomes official — ask before assuming it still holds.
