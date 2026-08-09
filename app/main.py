from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import detections, health, tracks
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Drone Multi-Sensor", lifespan=lifespan)

app.include_router(health.router, prefix="/api")
app.include_router(detections.router, prefix="/api")
app.include_router(tracks.router, prefix="/api")
