# Deploying to the Hetzner box

Target: `travel.foryayo.com` on `5.78.184.240`, behind the Caddy instance already serving
`foryayo.com`.

## One-time setup

### 1. DNS

An `A` record for `travel.foryayo.com` pointing at `5.78.184.240`. Verify before going further —
Caddy cannot issue a certificate until this resolves, and passkeys are bound to the hostname:

```bash
dig +short travel.foryayo.com
```

### 2. Clone the repo

```bash
git clone https://github.com/fred4mcaz/yayo_travel_tracker.git /srv/yayo_travel_tracker
```

### 3. Create the environment file

```bash
cp /srv/yayo_travel_tracker/deploy/.env.example /srv/yayo_travel_tracker/deploy/.env
```

Then edit it. `YAYO_RP_ID` must be exactly `travel.foryayo.com` — passkeys are cryptographically
bound to it, and changing it later invalidates every passkey you have registered.

Gmail and Anthropic credentials are not needed until stage 8; leave them blank and
`YAYO_EMAIL_INGEST_ENABLED=false` until then.

```bash
chmod 600 /srv/yayo_travel_tracker/deploy/.env
```

### 4. Add the Caddy site block

Append the contents of `deploy/Caddyfile.snippet` to the server's Caddyfile, then:

```bash
caddy validate --config /etc/caddy/Caddyfile
```

```bash
systemctl reload caddy
```

Caddy requests the certificate on the first inbound request. If it fails, check that ports 80 and
443 reach the box and that DNS has propagated.

### 5. First deploy

```bash
/srv/yayo_travel_tracker/deploy/deploy.sh
```

## Routine deploys

```bash
cd /srv/yayo_travel_tracker
```

```bash
./deploy/deploy.sh
```

The script pulls, snapshots the database with `sqlite3 .backup` before touching anything, rebuilds
the image, restarts, and polls `/api/health` for 30 seconds. It exits non-zero and prints the last
50 log lines if the app does not come up, so a broken deploy is loud rather than silent.

## Checking on it

```bash
docker compose -f /srv/yayo_travel_tracker/deploy/docker-compose.yml logs -f app
```

```bash
curl -s http://127.0.0.1:8081/api/health
```

## Rolling back

Images are rebuilt from the checkout, so a rollback is a checkout plus a redeploy:

```bash
cd /srv/yayo_travel_tracker
```

```bash
git checkout <previous-good-sha>
```

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

If a migration is at fault, restore the pre-deploy snapshot from `var/backups/` — see
[RUNBOOK.md](RUNBOOK.md).

## Build order

1. ~~Skeleton, container, deploy path, health endpoint~~ — done
2. Data model and CRUD API
3. Passkey auth
4. Frontend core and manual entry
5. Calendar and map
6. Passports, country entries, personal notes
7. Gap detection
8. Gmail ingest and review queue
9. Export, backup, ICS, docs

Every stage is deployable. The app is usable for manual entry from stage 4, before any of the email
machinery exists.
