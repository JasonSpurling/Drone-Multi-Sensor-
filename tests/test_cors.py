"""CORS is off by default (see app/config.py's CORS_ORIGINS) -- no
Access-Control-* headers at all, same as before CORSMiddleware existed
here -- and only added when DRONE_CORS_ORIGINS is set.

CORSMiddleware is only ever added to the FastAPI app once, at import time
in app/main.py, based on CORS_ORIGINS read at that same import time -- so
unlike most of this app's config (read fresh per-request via os.getenv),
toggling it for a test needs both app.config and app.main reloaded after
setting the env var. Reloading app.main doesn't touch app.db (a separate,
unreloaded module), so isolated_db's own monkeypatch of app.db.engine
stays in effect across the reload.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def reload_app_with_cors(monkeypatch):
    def _reload(origins: str):
        monkeypatch.setenv("DRONE_CORS_ORIGINS", origins)
        import app.config
        import app.main

        importlib.reload(app.config)
        importlib.reload(app.main)
        return app.main.app

    yield _reload

    # Restore both modules to their normal (no-CORS) state for every test
    # after this one.
    monkeypatch.delenv("DRONE_CORS_ORIGINS", raising=False)
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.main)


def test_no_cors_headers_by_default(isolated_db):
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/health", headers={"Origin": "https://example.com"})
        assert "access-control-allow-origin" not in r.headers


def test_cors_headers_present_for_an_allowed_origin(isolated_db, reload_app_with_cors):
    app = reload_app_with_cors("https://ops.example.com")
    with TestClient(app) as client:
        r = client.get("/api/health", headers={"Origin": "https://ops.example.com"})
        assert r.headers["access-control-allow-origin"] == "https://ops.example.com"


def test_cors_headers_absent_for_a_disallowed_origin(isolated_db, reload_app_with_cors):
    app = reload_app_with_cors("https://ops.example.com")
    with TestClient(app) as client:
        r = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in r.headers
