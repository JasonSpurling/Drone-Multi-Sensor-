from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

from app.db import count_active_tracks_all_sites, count_open_incidents_all_sites

router = APIRouter()

_tracks_active = Gauge("drone_tracks_active", "Current number of active tracks")
_incidents_open = Gauge("drone_incidents_open", "Current number of open incidents")


@router.get("/metrics", include_in_schema=False)
def metrics() -> PlainTextResponse:
    # Computed live at scrape time rather than tracked incrementally, so
    # these gauges can't drift from the database. Deployment-wide (every
    # site summed together), not per-site -- this endpoint is
    # conventionally left unauthenticated (see app/main.py), so there's no
    # Principal/site_id to scope it to.
    _tracks_active.set(count_active_tracks_all_sites())
    _incidents_open.set(count_open_incidents_all_sites())
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
