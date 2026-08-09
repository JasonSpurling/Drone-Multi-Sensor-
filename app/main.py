from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import detections, health, incidents, tracks
from app.db import init_db
from app.zones import load_zones_from_file


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
