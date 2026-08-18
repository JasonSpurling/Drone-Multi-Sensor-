from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

from app.db import list_incidents, list_tracks
from app.models import TrackStatus

router = APIRouter()

_tracks_active = Gauge("drone_tracks_active", "Current number of active tracks")
_incidents_open = Gauge("drone_incidents_open", "Current number of open incidents")


@router.get("/metrics", include_in_schema=False)
def metrics() -> PlainTextResponse:
    # Computed live at scrape time rather than tracked incrementally, so
    # these gauges can't drift from the database.
    _tracks_active.set(len(list_tracks(status=TrackStatus.ACTIVE.value)))
    _incidents_open.set(len(list_incidents(status="open")))
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
