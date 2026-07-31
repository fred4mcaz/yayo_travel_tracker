# Yayo travel

Self-hosted travel tracker at [travel.foryayo.com](https://travel.foryayo.com). Keeps past, ongoing,
and upcoming travel in one place with a calendar and a map, watches Gmail for booking confirmations
and proposes entries for review, and flags what's missing from each trip.

Single user, single SQLite file, no third-party runtime dependencies in the browser.

## Layout

| Path | What's in it |
|---|---|
| `backend/` | FastAPI app, SQLModel schema, Alembic migrations, background jobs |
| `frontend/` | React + Vite SPA, built to static assets and served by the backend |
| `data/` | Committed static assets: world GeoJSON, city coordinates, country rules |
| `var/` | **Runtime state — gitignored.** SQLite database, backups, stored email |
| `deploy/` | Dockerfile, compose file, Caddy snippet, deploy script |
| `docs/` | Setup, deploy, and operational runbook |

`data/` is read-only and versioned; `var/` is everything mutable. Back up `var/` and you have
everything.

## Local development

Backend, from `backend/`:

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

`npm run dev` serves on :5173 and proxies `/api` to :8000. Alternatively run `npm run build` once
and the backend will serve the built SPA from :8000 directly.

## Deploying

See [docs/DEPLOY.md](docs/DEPLOY.md). Short version: `git pull` on the server, then
`./deploy/deploy.sh`.

## Picking this up

Start with **[docs/HANDOFF.md](docs/HANDOFF.md)** — the domain model, current state,
what is next, and the traps already hit.

## The model, in one line

A trip is one international stay in one country: one passport, one arrival, many hotels.
Going to Vietnam then Thailand is two trips.
