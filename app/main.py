from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import detections, health, incidents, tracks, zones as zones_api
from app.db import init_db
from app.zones import load_zones_from_file

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_zones_from_file()
    yield


app = FastAPI(title="Drone Multi-Sensor", lifespan=lifespan)

app.include_router(health.router, prefix="/api")
app.include_router(detections.router, prefix="/api")
app.include_router(tracks.router, prefix="/api")
app.include_router(incidents.router, prefix="/api")
app.include_router(zones_api.router, prefix="/api")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")
