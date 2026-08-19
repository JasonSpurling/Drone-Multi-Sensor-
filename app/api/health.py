import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db import engine

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health(response: Response) -> dict:
    """A real readiness check, not just process liveness: a container/
    orchestrator that only checks "is the process up" can report healthy
    while the app can't actually serve a single real request because its
    database is unreachable (network partition, DB restart, wrong
    DRONE_DATABASE_URL) -- exactly the case a load balancer or Docker
    HEALTHCHECK needs to catch and route around/restart on.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check failed: database unreachable: %s", exc)
        response.status_code = 503
        return {"status": "unhealthy", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}
