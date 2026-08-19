from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import app


def test_health_reports_ok_when_database_is_reachable(isolated_db):
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "database": "ok"}


def test_health_reports_503_when_database_is_unreachable(isolated_db, monkeypatch):
    # Point app.db.engine at a database that can never be connected to,
    # simulating a real outage -- the health check must degrade gracefully
    # (503, not a 500 crash) so a load balancer/orchestrator can act on it.
    broken_engine = create_engine("postgresql+psycopg2://nouser:nopass@127.0.0.1:1/nodb", future=True)
    monkeypatch.setattr("app.api.health.engine", broken_engine)

    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 503
        assert r.json()["status"] == "unhealthy"
