import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from app.api import detections, health, incidents, sensors, tracks, zones as zones_api
from app.auth import require_api_key
from app.db import init_db
from app.logging_config import configure_logging
from app.zones import load_zones_from_file

configure_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_zones_from_file()
    logger.info("Drone Multi-Sensor API started")
    yield


app = FastAPI(title="Drone Multi-Sensor", lifespan=lifespan)

_auth = [Depends(require_api_key)]

# Left unauthenticated: conventional for liveness checks, and it exposes no
# data beyond {"status": "ok"}.
app.include_router(health.router, prefix="/api")

app.include_router(detections.router, prefix="/api", dependencies=_auth)
app.include_router(tracks.router, prefix="/api", dependencies=_auth)
app.include_router(incidents.router, prefix="/api", dependencies=_auth)
app.include_router(zones_api.router, prefix="/api", dependencies=_auth)
app.include_router(sensors.router, prefix="/api", dependencies=_auth)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")
