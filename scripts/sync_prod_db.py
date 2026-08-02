#!/usr/bin/env python3
"""Pull the live production database down for local review.

Local dev shows the same data that lives online. This overwrites the local
``var/travel.db`` with a fresh, consistent snapshot from the Hetzner box every
time it runs; it is wired into the local backend start (scripts/dev_backend.py).

It only ever *reads* the remote database -- a ``.backup`` snapshot streamed over
SSH -- and never writes anything back, so it cannot affect the official online
data. Local edits are therefore throwaway: the next start pulls a fresh copy and
overwrites them, which is exactly the intended "online is the only source of
truth" behaviour.

Failure is non-fatal: if the box is unreachable (offline, VPN down) the local
copy already on disk is left untouched and the backend still starts.
"""

import os
import subprocess
import sys
from pathlib import Path

REMOTE_HOST = "yayokun@5.78.184.240"
REMOTE_DIR = "/srv/yayo_travel_tracker"

# scripts/ -> repo root. Honour YAYO_VAR_DIR the same way app/config.py does, so
# a custom var dir in backend/.env still lands in the right place.
REPO_ROOT = Path(__file__).resolve().parents[1]
VAR_DIR = Path(os.environ.get("YAYO_VAR_DIR", REPO_ROOT / "var"))
LOCAL_DB = VAR_DIR / "travel.db"

SQLITE_MAGIC = b"SQLite format 3\x00"

# Snapshot to a temp file on the box (WAL-safe, unlike a raw copy), stream it
# out with cat, then clean up regardless of how cat exits.
REMOTE_SCRIPT = (
    f"cd {REMOTE_DIR} && "
    "t=$(mktemp) && "
    "sqlite3 var/travel.db \".backup '$t'\" && "
    'cat "$t"; rc=$?; rm -f "$t"; exit $rc'
)


def main() -> int:
    # Defence in depth: never let this run against a production config. The box
    # serves the app via Docker, not this script, so this should never trigger --
    # but if it somehow did, pulling from itself would be pointless at best.
    if os.environ.get("YAYO_SITE_ORIGIN", "").startswith("https://"):
        print("sync_prod_db: refusing to run under a production config.", file=sys.stderr)
        return 0

    print(f"Syncing local DB from {REMOTE_HOST} ...")
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",  # fail fast instead of prompting for a password
                "-o", "ConnectTimeout=10",
                REMOTE_HOST,
                REMOTE_SCRIPT,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("sync_prod_db: ssh not found; keeping the existing local DB.", file=sys.stderr)
        return 0

    if result.returncode != 0 or not result.stdout.startswith(SQLITE_MAGIC):
        detail = result.stderr.decode(errors="replace").strip() or "no SQLite data returned"
        existing = "existing local DB" if LOCAL_DB.exists() else "empty local DB"
        print(
            f"sync_prod_db: pull failed ({detail}); keeping the {existing}.",
            file=sys.stderr,
        )
        return 0

    VAR_DIR.mkdir(parents=True, exist_ok=True)
    # Write beside the target, then atomically swap it in, so an interrupted pull
    # can never leave a half-written database.
    tmp = LOCAL_DB.with_suffix(".db.incoming")
    tmp.write_bytes(result.stdout)
    os.replace(tmp, LOCAL_DB)
    # The snapshot is a standalone DB; drop any stale WAL sidecars from a previous
    # local run so they cannot shadow the fresh data.
    for sidecar in (LOCAL_DB.with_name("travel.db-wal"), LOCAL_DB.with_name("travel.db-shm")):
        sidecar.unlink(missing_ok=True)

    print(f"    pulled {len(result.stdout):,} bytes into {LOCAL_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
