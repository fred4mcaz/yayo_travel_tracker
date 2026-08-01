# Handoff

Everything a fresh session needs to pick this up. Read this first, then
[DEPLOY.md](DEPLOY.md) when you need to ship.

**Live:** https://travel.foryayo.com · **Deployed commit:** `9bb3e75` ·
**178 tests passing**, ruff and typecheck clean.

---

## 1. What this is

A personal travel tracker for one user (Eduardo, `req4233@gmail.com`), self-hosted
on his Hetzner box. Private repo, passkey-only auth, no third-party runtime
dependencies in the browser.

It answers one question well: **for each upcoming international stay — which
country, on which passport, which hotels, and have I forgotten to book any
nights?**

---

## 2. The domain model — read this before changing anything

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

---

## 3. Current state

| Area | State |
|---|---|
| Deploy | One command on the server, auto-backup first, healthy in ~4s |
| Auth | Passkeys only. 1 passkey enrolled, 10 unused recovery codes |
| Data model | 14 tables, 3 Alembic migrations, runs on container start |
| Trip entry | Working end to end: country picker, city autocomplete, date steppers |
| Missing hotels | Working — see below |
| Calendar | Month grid, trips as bars, notes as dots |
| Map | Canvas world map, country fill, city pins, route arcs. No tile server |
| Passports | MX (expires 2036-04-06) and US (expires 2035-11-17), last-4 only |
| Gmail ingest | **Live and on.** Fetch → filter → extract → propose; you accept. §8 |
| Export / backup | Nightly DB backup on deploy only. No export UI yet |

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

## 4. Where things live

```
backend/app/
  models.py            14 tables. Times are naive local wall-clock, by design
  schemas.py           Create/Update payloads, separate from tables
  countries.py         GENERATED — run scripts/build_countries.py
  api/trips.py         Trips, hotels, travel, paperwork, passport-used
  api/{auth,passports,notes,geo}.py
  services/trips.py    All derived state: label, status, country, unbooked
  services/auth.py     WebAuthn, sessions, recovery codes — all hashed
  services/geocode.py  City → lat/lon and autocomplete, from bundled data
frontend/src/
  views/TripDetail.tsx The country-first detail panel
  views/{Trips,Calendar,Map,Settings,Auth}.tsx
  components/          CityInput, CountrySelect, DateField, StayForm, LegForm, Sheet
  lib/countries.ts     GENERATED — source of truth for country names
data/geo/              GENERATED — run scripts/build_geo.py
deploy/                Dockerfile, compose, Caddy snippet, deploy.sh
var/                   RUNTIME, gitignored. SQLite + backups. Back this up
```

`data/` is committed and read-only. `var/` is everything mutable — copy it and
you have the whole app's state.

---

## 5. Running and deploying

Backend, from `backend/` (no env vars needed; paths resolve to the repo root):

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Tests:

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
```

Frontend, from `frontend/`:

```bash
npm run build
```

Deploy — **the user pushes, you pull.** He runs `git push`; you then run:

```bash
ssh yayokun@5.78.184.240 'cd /srv/yayo_travel_tracker && ./deploy/deploy.sh'
```

> **Do not skip this.** Three commits were once left undeployed across two
> turns because "push and I'll deploy" was said and never done. He reported the
> features as missing and was right. After he pushes, deploy, then verify the
> shipped bundle actually contains the change:
>
> ```bash
> ssh yayokun@5.78.184.240 'ASSET=$(curl -sS https://travel.foryayo.com/ | grep -o "/assets/index-[A-Za-z0-9_-]*\.js"); curl -sS "https://travel.foryayo.com$ASSET" | grep -c "some new string"'
> ```

You cannot `git push` — the permission classifier blocks it. Ask him to push.

---

## 6. The server

- `5.78.184.240`, user `yayokun`, Ubuntu 26.04, **1.9 GB RAM / 4 GB swap**
- Checkout at `/srv/yayo_travel_tracker`, owned by `yayokun`
- Docker 29.1.3 + compose v2 (`docker-compose-v2`, not `docker-compose-plugin`)
- **Caddy on the host** serves `travel.foryayo.com` → `127.0.0.1:8081`. It also
  serves other sites; never restart it carelessly, and back up
  `/etc/caddy/Caddyfile` before editing
- Private repo, cloned via a read-only deploy key at `~/.ssh/yayo_travel_deploy`
  under the host alias `github-yayo-travel`
- `sudo` needs his password — you cannot use it. Anything requiring root has to
  be handed to him as **one command per fenced block** (see his CLAUDE.md; a
  multi-line paste once fed a line into a sudo password prompt)

The frontend builds fine on that box despite the RAM; that was checked.

---

## 7. Traps already hit — do not rediscover these

**Windows dev machine, Linux server.**
- `.gitattributes` pins `eol=lf` for shell scripts, Dockerfile and YAML. Without
  it, `deploy.sh` checks out CRLF and Linux fails with a misleading "not found".
- Git on Windows does not set the executable bit. `deploy.sh` and
  `entrypoint.sh` are mode `100755` in the index via `git update-index --chmod=+x`.
- Git does not track empty directories. `data/geo` and `data/rules` hold
  `.gitkeep` files; deleting them breaks `COPY data/` in the image build.
- `.dockerignore` exists because the build otherwise copied `backend/.venv` and
  `node_modules` into the image.

**Runtime.**
- `deploy.sh` git-pulls itself. Its whole body is inside `main()` so bash parses
  the file before executing; without that it resumes at a stale byte offset and
  silently skips commands.
- A bind mount keeps the *host* directory's ownership. The container runs as the
  host uid (`YAYO_UID`) so it can write `var/`.
- SQLite cannot add a `NOT NULL` column without `server_default`.

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

## 8. Gmail ingest (stage 8) — built, deployed, and on

The reason the forms ask for so little: a booking email becomes a trip, with a
human accept in the middle. Full phased history and the lessons each phase cost
are in [STAGE8_PLAN.md](STAGE8_PLAN.md).

**The cycle, every 10 minutes** — APScheduler, started from the app lifespan
(`services/scheduler.py`):

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
   never reaches the API. A privacy control, not a cost one — extend the
   allow-list as new senders show up.
3. **Extract** — `services/extraction.py`. Candidates go to the Claude API
   behind **strict** tool schemas: `claude-haiku-4-5` triages ("is this really a
   booking?"), and only on a yes does `claude-sonnet-5` pull out the structured
   booking. Results land in `extraction` as `status=pending`; a malformed
   response is validated out and persists nothing. The model is injected behind
   a Protocol, so the whole pipeline tests offline.
4. **Match** — `services/review.py`. `find_matching_trip` attaches a booking to
   an existing trip by same-country **and** ±2-day date overlap, otherwise
   proposes a new trip. A different-country booking becomes its own trip **by
   construction** — the one-country rule holds without ever failing the guard.
5. **Review** — `api/review.py`, `views/Review.tsx`. Proposals appear in the
   **Review tab** with a badge count. Accept or dismiss; correct a field the
   model missed first (overrides are allow-listed to booking fields). **Accepting
   is the only thing that writes trip data** — matching and dismissing touch
   none. That boundary is asserted as a row-count test, and a *failed* accept
   leaves no orphan trip.

**The gate.** `YAYO_EMAIL_INGEST_ENABLED` controls all of it. Off: nothing
starts, connects, or reads a credential. On but a credential missing: the
container **fails to boot loudly**, naming the unset vars (never their values).
It is now **on** in `deploy/.env`, with the Gmail app password and Anthropic key
in place. `POST /api/review/poll` (the "Check email now" button) runs one cycle
on demand, gated identically — 409 with the reason if off or unconfigured.

**Verified live** against the real mailbox (2026-07-31): baselined at UID 168354
ingesting nothing, then picked up new mail going forward; the filter gated
correctly and triage produced no false proposals. Costs a little now — Haiku
per triaged candidate, Sonnet only when triage says yes.

---

## 9. What's left

- **Notes creation UI.** The notes API is complete and notes render on the
  calendar and trip detail, but there is still no way to create one from the UI.
- **`api/notes.py` bug (flagged, task chip open).** `list_notes` reads
  `Trip.title`, which was deleted in `b40ec0a`, so `GET /api/notes` 500s for any
  note that has a `trip_id`. No note has one yet, so it has not surfaced. Swap to
  the derived label (see `api/review.py`'s `_trip_label`).
- **`Requirement` rows** render read-only on trip detail; nothing creates them.
- **Export, nightly backup cron, ICS feed (stage 9)** are unbuilt. Only the
  pre-deploy backup in `deploy.sh` exists.
- **Visa-free advisory dataset** (`data/rules/visa-free.json`,
  `entry-requirements.json`) was designed but never built. `email-filter.json`
  is the only thing in `data/rules/` so far.

### Smaller things he was once offered
Entry-card/visa reminders per country, passport-expiry warnings against entry
dates, CSV/JSON export, an ICS subscribe feed.

---

## 10. How he works

- **He iterates by using it and reporting friction.** Expect the model to keep
  moving. When he corrects the design, take the correction literally and delete
  what it displaces — he explicitly said not to keep backwards compatibility.
- **Verify in a browser, do not trust the code.** Every input bug in this project
  was found by driving the real UI, never by reading. Mint a local session with
  `create_session` and set the cookie via JS to skip the passkey.
- **Screenshots letterbox.** Trust `getBoundingClientRect` over eyeballing a
  scaled screenshot; two "bugs" were misread that way.
- This is still a **development environment**. He has said to delete freely.
  That will change when it becomes official — ask before assuming it still holds.
