from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import app


def test_health_reports_ok_when_database_is_reachable(isolated_db):
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "database": "ok"}


def test_health_reports_503_when_database_is_unreachable(isolated_db, monkeypatch):
    # Start the app against the real (working) isolated test database first
    # -- app startup itself needs a working DB (init_db() isn't, and
    # shouldn't be, exception-swallowing) -- then swap in a database that
    # can never be connected to, simulating an outage that happens *after*
    # startup. The health check must degrade gracefully (503, not a 500
    # crash) so a load balancer/orchestrator can act on it.
    with TestClient(app) as client:
        broken_engine = create_engine("postgresql+psycopg2://nouser:nopass@127.0.0.1:1/nodb", future=True)
        monkeypatch.setattr("app.db.engine", broken_engine)

        r = client.get("/api/health")
        assert r.status_code == 503
        assert r.json()["status"] == "unhealthy"
