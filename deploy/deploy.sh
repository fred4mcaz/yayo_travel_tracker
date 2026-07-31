#!/usr/bin/env bash
# Deploy the current main branch on the server.
#   cd /srv/yayo_travel_tracker && ./deploy/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

if [ ! -f deploy/.env ]; then
  echo "error: deploy/.env is missing. Copy deploy/.env.example and fill it in." >&2
  exit 1
fi

echo "==> Pulling latest"
git pull --ff-only

echo "==> Backing up the database before rebuild"
if [ -f var/travel.db ]; then
  mkdir -p var/backups
  STAMP="$(date +%Y%m%d-%H%M%S)"
  # .backup is safe on a live WAL database; a file copy is not.
  sqlite3 var/travel.db ".backup 'var/backups/pre-deploy-${STAMP}.sqlite'"
  gzip -f "var/backups/pre-deploy-${STAMP}.sqlite"
  echo "    saved var/backups/pre-deploy-${STAMP}.sqlite.gz"
else
  echo "    no database yet, skipping"
fi

echo "==> Building and restarting"
docker compose -f deploy/docker-compose.yml up -d --build

echo "==> Waiting for health"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8081/api/health >/dev/null 2>&1; then
    echo "    healthy after ${i}s"
    echo
    echo "Deployed. https://travel.foryayo.com"
    exit 0
  fi
  sleep 1
done

echo "error: app did not become healthy within 30s. Recent logs:" >&2
docker compose -f deploy/docker-compose.yml logs --tail 50 app >&2
exit 1
