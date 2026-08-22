import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api import (
    audit_log,
    authorized_operators,
    detections,
    health,
    incidents,
    metrics,
    reports,
    sensor_registry,
    sensors,
    sites,
    tracks,
)
from app.api import keys as keys_api
from app.api import live as live_api
from app.api import zones as zones_api
from app.config import (
    AUDIT_LOG_RETENTION_DAYS,
    BEHAVIOR_SWEEP_INTERVAL_SECONDS,
    CORS_ORIGINS,
    DETECTION_RETENTION_DAYS,
    RETENTION_SWEEP_INTERVAL_SECONDS,
    TRACK_RETENTION_DAYS,
)
from app.db import (
    init_db,
    list_sites,
    list_tracks,
    purge_old_audit_log,
    purge_old_detections,
    purge_old_tracks,
)
from app.incidents import check_formation_incidents, check_shadowing_incidents
from app.live import set_event_loop
from app.logging_config import configure_logging
from app.metrics import http_exceptions_total, http_request_duration_seconds, http_requests_total
from app.models import TrackStatus
from app.sites import ensure_default_site
from app.util import utcnow
from app.zones import load_zones_from_file

configure_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _retention_sweep_loop() -> None:
    if DETECTION_RETENTION_DAYS <= 0 and TRACK_RETENTION_DAYS <= 0 and AUDIT_LOG_RETENTION_DAYS <= 0:
        return
    while True:
        await asyncio.sleep(RETENTION_SWEEP_INTERVAL_SECONDS)
        now = utcnow()
        if DETECTION_RETENTION_DAYS > 0:
            cutoff = now - timedelta(days=DETECTION_RETENTION_DAYS)
            removed = await asyncio.to_thread(purge_old_detections, cutoff)
            if removed:
                logger.info("Retention sweep purged %d detection(s) older than %s", removed, cutoff)
        if TRACK_RETENTION_DAYS > 0:
            cutoff = now - timedelta(days=TRACK_RETENTION_DAYS)
            removed = await asyncio.to_thread(purge_old_tracks, cutoff)
            if removed:
                logger.info("Retention sweep purged %d track(s) older than %s", removed, cutoff)
        if AUDIT_LOG_RETENTION_DAYS > 0:
            cutoff = now - timedelta(days=AUDIT_LOG_RETENTION_DAYS)
            removed = await asyncio.to_thread(purge_old_audit_log, cutoff)
            if removed:
                logger.info("Retention sweep purged %d audit log entries older than %s", removed, cutoff)


def _run_behavior_sweep() -> None:
    # Per-site: formation/shadowing compare active tracks pairwise, and
    # two tracks at different physical sites being "in formation" with
    # each other is meaningless -- each site's tracks are only ever
    # compared against that same site's other tracks.
    for site in list_sites():
        assert site.id is not None
        active_tracks = list_tracks(site_id=site.id, status=TrackStatus.ACTIVE.value)
        check_formation_incidents(active_tracks)
        check_shadowing_incidents(active_tracks)


async def _behavior_sweep_loop() -> None:
    """Formation and shadowing (app/behavior.py) compare multiple active
    tracks against each other -- a different computational shape than the
    per-detection checks (zone incursion, loitering) that run inline on
    every update, so this runs as a periodic sweep instead, the same
    pattern as the retention sweep above.
    """
    while True:
        await asyncio.sleep(BEHAVIOR_SWEEP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_run_behavior_sweep)
        except Exception:
            logger.exception("Behavior sweep failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_zones_from_file(site_id=ensure_default_site())
    # app.live.publish() is called from sync endpoint code (a worker
    # thread, not this event loop) and needs a reference to this loop to
    # safely hand events back to it -- see that module's docstring.
    set_event_loop(asyncio.get_running_loop())
    logger.info("Drone Multi-Sensor API started")
    retention_task = asyncio.create_task(_retention_sweep_loop())
    behavior_task = asyncio.create_task(_behavior_sweep_loop())
    try:
        yield
    finally:
        for task in (retention_task, behavior_task):
            task.cancel()
        for task in (retention_task, behavior_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task


class _RequestMetricsMiddleware(BaseHTTPMiddleware):
    """Generic HTTP-layer observability (request count/latency by route,
    and a count of requests that raised an unhandled exception) --
    independent of the domain-specific counters in app/metrics.py, which
    only cover the detection-ingest path. See that module's docstring on
    http_requests_total for why this labels by route *template*, not raw
    path.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Starlette's routing has already set request.scope["route"]
            # by the time an endpoint's own exception propagates back up
            # through call_next, even though the response itself never got
            # built -- so the path template is still available here.
            route = request.scope.get("route")
            path = route.path if route is not None else "unmatched"
            http_exceptions_total.labels(method=request.method, path=path).inc()
            http_request_duration_seconds.labels(method=request.method, path=path).observe(
                time.perf_counter() - start
            )
            raise
        route = request.scope.get("route")
        path = route.path if route is not None else "unmatched"
        http_requests_total.labels(method=request.method, path=path, status=str(response.status_code)).inc()
        http_request_duration_seconds.labels(method=request.method, path=path).observe(
            time.perf_counter() - start
        )
        return response


app = FastAPI(title="Drone Multi-Sensor", lifespan=lifespan)
app.add_middleware(_RequestMetricsMiddleware)

if CORS_ORIGINS:
    # Off by default (empty list -- CORSMiddleware isn't even added), so a
    # deployment that never sets DRONE_CORS_ORIGINS behaves exactly as
    # before this existed: no CORS headers, cross-origin browser JS
    # blocked. Only needed when the dashboard (or another frontend) is
    # hosted on a different origin than this API -- see app/config.py.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,  # this app authenticates via X-API-Key, not cookies
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Left unauthenticated: conventional for liveness/scrape endpoints, and
# neither exposes anything beyond aggregate operational state.
app.include_router(health.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")

# Every route below applies its own per-route role requirement (rather
# than a router-level blanket dependency): each handler needs the
# authenticated Principal itself now, not just an auth check, to know
# which site's data to read/write (see app/auth.py's Principal.site_id).
app.include_router(detections.router, prefix="/api")
app.include_router(tracks.router, prefix="/api")
app.include_router(incidents.router, prefix="/api")
app.include_router(zones_api.router, prefix="/api")
app.include_router(sensors.router, prefix="/api")
app.include_router(sensor_registry.router, prefix="/api")
app.include_router(authorized_operators.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(sites.router, prefix="/api")
app.include_router(audit_log.router, prefix="/api")
app.include_router(keys_api.router, prefix="/api")
app.include_router(live_api.router)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")
