#!/usr/bin/env bash
# Container entrypoint: migrate, then serve.
set -euo pipefail

echo "==> Running database migrations"
# Runs on every start and is a no-op when already current, so a deploy that
# adds no migration costs nothing.
alembic upgrade head

echo "==> Starting uvicorn"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips '*'
