import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app import __version__
from app import db as db_module

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

    Looks up db_module.engine at call time (not `from app.db import
    engine` at module load) so it stays correct if the engine is ever
    swapped after import -- which the test suite's isolated-database
    fixture does for every test.
    """
    try:
        with db_module.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any DB failure
        # here (SQLAlchemy's own errors, a raw driver error, a network
        # timeout) means the same thing to a caller -- unhealthy -- so
        # there's no narrower exception type that would change what this
        # does; a liveness check that itself crashes on an unexpected
        # driver error defeats its own purpose.
        logger.warning("Health check failed: database unreachable: %s", exc)
        response.status_code = 503
        return {"status": "unhealthy", "database": "unreachable", "version": __version__}
    return {"status": "ok", "database": "ok", "version": __version__}
