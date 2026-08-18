import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from app.api import (
    authorized_operators,
    detections,
    health,
    incidents,
    metrics,
    sensor_registry,
    sensors,
    tracks,
    zones as zones_api,
)
from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_role
from app.config import DETECTION_RETENTION_DAYS, RETENTION_SWEEP_INTERVAL_SECONDS
from app.db import init_db, purge_old_detections
from app.logging_config import configure_logging
from app.util import utcnow
from app.zones import load_zones_from_file

configure_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

_viewer_auth = [Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN))]


async def _retention_sweep_loop() -> None:
    if DETECTION_RETENTION_DAYS <= 0:
        return
    while True:
        await asyncio.sleep(RETENTION_SWEEP_INTERVAL_SECONDS)
        cutoff = utcnow() - timedelta(days=DETECTION_RETENTION_DAYS)
        removed = await asyncio.to_thread(purge_old_detections, cutoff)
        if removed:
            logger.info("Retention sweep purged %d detection(s) older than %s", removed, cutoff)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_zones_from_file()
    logger.info("Drone Multi-Sensor API started")
    sweep_task = asyncio.create_task(_retention_sweep_loop())
    try:
        yield
    finally:
        sweep_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep_task


app = FastAPI(title="Drone Multi-Sensor", lifespan=lifespan)

# Left unauthenticated: conventional for liveness/scrape endpoints, and
# neither exposes anything beyond aggregate operational state.
app.include_router(health.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")

# POST /detections and the incident acknowledge/resolve actions apply their
# own per-route role requirements (ingest vs operator); everything else
# here is read-only and just needs any authenticated (viewer+) key.
app.include_router(detections.router, prefix="/api")
app.include_router(tracks.router, prefix="/api", dependencies=_viewer_auth)
app.include_router(incidents.router, prefix="/api")
app.include_router(zones_api.router, prefix="/api", dependencies=_viewer_auth)
app.include_router(sensors.router, prefix="/api", dependencies=_viewer_auth)
app.include_router(sensor_registry.router, prefix="/api", dependencies=_viewer_auth)
app.include_router(authorized_operators.router, prefix="/api", dependencies=_viewer_auth)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")
