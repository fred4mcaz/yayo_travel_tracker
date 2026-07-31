# Build context is the repository root:
#   docker build -f deploy/Dockerfile .

# --- frontend build ---------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
# `npm ci` needs a lockfile; fall back to `install` on the very first build
# before one has been committed.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend/ ./
RUN npm run build

# --- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    YAYO_VAR_DIR=/data \
    YAYO_DATA_DIR=/srv/data

# sqlite3 CLI is used by the nightly `.backup` job.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /srv/backend/
COPY data/ /srv/data/
COPY --from=frontend /build/dist /srv/frontend/dist

# Run unprivileged. /data is a mounted volume owned by this uid.
RUN useradd --system --uid 10001 --create-home yayo \
    && mkdir -p /data \
    && chown -R yayo:yayo /data /srv
USER yayo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
