#!/usr/bin/env python3
"""Reconcile every trip's system-sourced Requirement rows against the current
entry policy -- cache and curated overrides only, no network.

Why this exists: an entry-policy *override* (data/rules/entry-policy-overrides.json)
takes effect for the *reading* immediately, but the trip's materialized
Requirement rows are only reconciled when `sync_requirements` next runs -- i.e.
on the next edit to that trip. So after shipping an override that removes a
requirement (e.g. correcting the UK from evisa+visa to visa-free+ETA), an
already-recorded trip keeps its now-stale system row (a spurious "Visa") until
someone touches it. This script forces that reconciliation for all trips at once.

It is safe to run any time: `sync_requirements` is idempotent and only ever
touches `source=system` rows still at `todo`; a row the traveller advanced or an
email confirmed is never altered. It passes `model=None`, so it makes **no LLM
calls and no network requests** -- it reconciles purely against what is already
cached or overridden. A trip whose policy is not yet cached is simply left as is
(its rows fill in on the next normal edit).

Run it inside the app container against the live DB, after a deploy that ships an
override change (the image copies scripts/ to /srv/scripts/):

    docker compose -f deploy/docker-compose.yml exec -T app \
        python /srv/scripts/resync_requirements.py

or locally against the dev DB with the backend venv:

    backend/.venv/Scripts/python.exe scripts/resync_requirements.py
"""

import sys
from pathlib import Path

# Allow running as a plain script: make the backend package importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import Requirement, Trip  # noqa: E402
from app.services.trips import sync_requirements  # noqa: E402


def main() -> int:
    changed = 0
    with Session(engine) as session:
        trips = session.exec(select(Trip)).all()
        for trip in trips:
            before = {
                (r.kind, r.status)
                for r in session.exec(
                    select(Requirement).where(Requirement.trip_id == trip.id)
                ).all()
            }
            # model=None: cache/override only, never a fetch.
            sync_requirements(session, trip, model=None)
            after = {
                (r.kind, r.status)
                for r in session.exec(
                    select(Requirement).where(Requirement.trip_id == trip.id)
                ).all()
            }
            if before != after:
                changed += 1
                print(f"trip {trip.id}: requirements reconciled {sorted(before)} -> {sorted(after)}")
    print(f"done: {len(trips)} trips checked, {changed} reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
