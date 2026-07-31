#!/usr/bin/env bash
# Deploy the current main branch on the server.
#   cd /srv/yayo_travel_tracker && ./deploy/deploy.sh
set -euo pipefail

# Everything lives inside main() so bash parses the whole script before running
# any of it. This script `git pull`s itself, and bash otherwise reads scripts
# lazily by byte offset -- rewriting the file mid-execution makes it resume at a
# stale offset and silently skip or mangle later commands. That is not
# hypothetical: it already cost one deploy that skipped the mkdir below.
main() {
  cd "$(dirname "$0")/.."

  if [ ! -f deploy/.env ]; then
    echo "error: deploy/.env is missing. Copy deploy/.env.example and fill it in." >&2
    exit 1
  fi

  # The container runs as this uid so it can write to the bind-mounted var/.
  YAYO_UID="$(id -u)"
  YAYO_GID="$(id -g)"
  export YAYO_UID YAYO_GID

  # Create these before compose does. Docker creates a missing bind-mount source
  # as root, which the container then cannot write to.
  mkdir -p var/backups

  if [ ! -w var ]; then
    echo "error: var/ is not writable by $(id -un). It was probably created by" >&2
    echo "       Docker as root on an earlier run. Fix with:" >&2
    echo "         sudo chown -R $(id -u):$(id -g) var" >&2
    exit 1
  fi

  echo "==> Pulling latest"
  git pull --ff-only

  echo "==> Backing up the database before rebuild"
  if [ -f var/travel.db ]; then
    local stamp
    stamp="$(date +%Y%m%d-%H%M%S)"
    # .backup is safe on a live WAL database; a file copy is not.
    sqlite3 var/travel.db ".backup 'var/backups/pre-deploy-${stamp}.sqlite'"
    gzip -f "var/backups/pre-deploy-${stamp}.sqlite"
    echo "    saved var/backups/pre-deploy-${stamp}.sqlite.gz"
  else
    echo "    no database yet, skipping"
  fi

  echo "==> Building and restarting"
  docker compose -f deploy/docker-compose.yml up -d --build

  echo "==> Waiting for health"
  local i
  for i in $(seq 1 45); do
    if curl -fsS http://127.0.0.1:8081/api/health >/dev/null 2>&1; then
      echo "    healthy after ${i}s"
      echo
      echo "Deployed. https://travel.foryayo.com"
      return 0
    fi
    sleep 1
  done

  echo "error: app did not become healthy within 45s. Recent logs:" >&2
  docker compose -f deploy/docker-compose.yml logs --tail 50 app >&2
  return 1
}

main "$@"
