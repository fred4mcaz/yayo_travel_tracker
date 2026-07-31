"""Application entrypoint.

Serves the JSON API under /api and the built React SPA for everything else.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import notes, passports, trips
from app.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)
log = logging.getLogger("yayo")

app = FastAPI(title="Yayo travel", docs_url=None, redoc_url=None, openapi_url=None)

# The SPA build lands here in the Docker image; absent during backend-only dev.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


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
    return {
        "status": "ok",
        "rp_id": settings.rp_id,
        "email_ingest_enabled": settings.email_ingest_enabled,
    }


# API routes must be registered before the SPA catch-all below, otherwise the
# catch-all pattern matches first and shadows every one of them.
app.include_router(trips.router)
app.include_router(passports.router)
app.include_router(notes.router)


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
