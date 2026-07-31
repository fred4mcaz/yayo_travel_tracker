"""Application entrypoint.

Serves the JSON API under /api and the built React SPA for everything else.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.api import auth, notes, passports, trips
from app.config import get_settings
from app.db import engine
from app.services.auth import (
    has_any_passkey,
    issue_enrollment_token,
    purge_expired_sessions,
)

settings = get_settings()
logging.basicConfig(level=settings.log_level)
log = logging.getLogger("yayo")

# The SPA build lands here in the Docker image; absent during backend-only dev.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    with Session(engine) as session:
        removed = purge_expired_sessions(session)
        if removed:
            log.info("purged %d expired session(s)", removed)

        if not has_any_passkey(session):
            # No way into the app yet. Print a one-time enrollment link; only its
            # hash is stored, so this log line is the only place it exists.
            token = issue_enrollment_token(session)
            log.warning(
                "\n"
                "=========================================================\n"
                " No passkey registered yet. Open this once to enroll:\n"
                "   %s/enroll?token=%s\n"
                " This link works once and is not recoverable from the DB.\n"
                "=========================================================",
                settings.site_origin,
                token,
            )
    yield


app = FastAPI(
    title="Yayo travel",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # No third-party anything: the map is bundled, so we can lock this right down.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/api/health")
def health() -> dict:
    """Unauthenticated: the Docker healthcheck and Caddy both hit this."""
    return {
        "status": "ok",
        "rp_id": settings.rp_id,
        "email_ingest_enabled": settings.email_ingest_enabled,
    }


@app.get("/geo/countries.geojson")
def country_outlines():
    """Country outlines for the map.

    Left unauthenticated on purpose: this is public-domain Natural Earth data
    identical for every visitor and reveals nothing about the user, and keeping
    it out of the session-gated paths lets it be cached hard. It never changes
    between deploys, so it is marked immutable.
    """
    path = settings.data_dir / "geo" / "countries.min.geojson"
    if not path.is_file():
        return JSONResponse(
            {"detail": "Map data missing. Run scripts/build_geo.py."}, status_code=503
        )
    return FileResponse(
        path,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# Auth endpoints police themselves — /status and the login flow must be
# reachable while logged out.
app.include_router(auth.router)

# Everything holding trip data is gated at the router level rather than
# per-endpoint, so a route added later cannot accidentally ship unprotected.
protected = [Depends(auth.require_auth)]
app.include_router(trips.router, dependencies=protected)
app.include_router(passports.router, dependencies=protected)
app.include_router(notes.router, dependencies=protected)


# The SPA catch-all must come last: it matches every path, so any route
# registered after it would be shadowed and never reached.
if FRONTEND_DIST.is_dir():
    app.mount(
        "/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets"
    )

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """Serve the SPA shell for any non-API path so client routing works."""
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

else:  # pragma: no cover - developer convenience only
    log.warning("frontend/dist not found — API only. Run `npm run build` in frontend/.")
