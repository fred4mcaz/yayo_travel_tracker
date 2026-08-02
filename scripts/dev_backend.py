#!/usr/bin/env python3
"""Local dev backend launcher: refresh data from production, then serve.

Wired into .claude/launch.json and documented in the README as the way to run
the backend locally. It pulls a fresh snapshot of the online database
(scripts/sync_prod_db.py) so local review shows the same data as the live site,
then hands off to uvicorn.

uvicorn runs its own ``--reload`` loop, so this process -- and the pull above --
runs once per launch, not on every code edit.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# 1. Refresh the local DB from production (non-fatal; offline still starts).
subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_prod_db.py")])

# 2. Serve exactly as a plain `uvicorn app.main:app --reload` would.
raise SystemExit(
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
        cwd=str(BACKEND),
    ).returncode
)
