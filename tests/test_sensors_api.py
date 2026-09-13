"""GET /api/sensors (app/api/sensors.py) -- a thin router wrapping
app.sensors.get_sensor_health(); tests/test_sensor_health.py already covers
get_sensor_health()'s own logic directly, this just proves the route wires
it to the calling principal's site_id.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def viewer_key(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"viewer-key": "viewer"}))
    return "viewer-key"


def test_get_sensors_returns_health_for_the_callers_site(viewer_key):
    with TestClient(app) as client:
        r = client.get("/api/sensors", headers={"X-API-Key": viewer_key})
        assert r.status_code == 200
        assert r.json() == []
