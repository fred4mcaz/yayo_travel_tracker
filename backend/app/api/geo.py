"""City lookup for the entry form.

Backed entirely by the bundled GeoNames subset, so typing a city name never
reaches a third party.
"""

from fastapi import APIRouter, Query

from app.services.geocode import suggest

router = APIRouter(prefix="/api/geo", tags=["geo"])


@router.get("/cities")
def city_suggestions(
    country: str = Query(min_length=2, max_length=2),
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=8, ge=1, le=25),
) -> list[dict]:
    """Cities in a country matching a partial name, most populous first."""
    return suggest(country, q, limit)
