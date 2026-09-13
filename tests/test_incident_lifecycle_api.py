"""POST /api/incidents/{id}/{acknowledge,investigate,resolve} (app/api/
incidents.py) -- the not-found and already-in-that-state edge cases of the
lifecycle transitions. tests/test_audit_log.py already covers the happy
path (open -> acknowledged -> investigating -> resolved) and its audit
trail; this covers what each transition rejects.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


@pytest.fixture
def admin_key(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"admin-key": "admin"}))
    return "admin-key"


def _open_an_incident(client: TestClient, headers: dict) -> int:
    client.post("/api/detections", json=DETECTION_BODY, headers=headers)
    # Inside app/zones.seed.json's bundled restricted zone.
    zone_body = {**DETECTION_BODY, "latitude": 51.5, "longitude": -0.10}
    client.post("/api/detections", json=zone_body, headers=headers)
    incidents = client.get("/api/incidents", headers=headers).json()
    assert incidents, "expected the zone-incursion detection to open an incident"
    return incidents[0]["id"]


def test_acknowledge_a_nonexistent_incident_404s(admin_key):
    with TestClient(app) as client:
        r = client.post("/api/incidents/999999/acknowledge", headers={"X-API-Key": admin_key})
        assert r.status_code == 404


def test_acknowledging_an_already_acknowledged_incident_409s(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        incident_id = _open_an_incident(client, headers)
        r1 = client.post(f"/api/incidents/{incident_id}/acknowledge", headers=headers)
        assert r1.status_code == 200
        r2 = client.post(f"/api/incidents/{incident_id}/acknowledge", headers=headers)
        assert r2.status_code == 409


def test_investigate_a_nonexistent_incident_404s(admin_key):
    with TestClient(app) as client:
        r = client.post("/api/incidents/999999/investigate", headers={"X-API-Key": admin_key})
        assert r.status_code == 404


def test_resolve_a_nonexistent_incident_404s(admin_key):
    with TestClient(app) as client:
        r = client.post("/api/incidents/999999/resolve", headers={"X-API-Key": admin_key})
        assert r.status_code == 404


def test_resolving_an_already_resolved_incident_409s(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        incident_id = _open_an_incident(client, headers)
        r1 = client.post(f"/api/incidents/{incident_id}/resolve", headers=headers)
        assert r1.status_code == 200
        r2 = client.post(f"/api/incidents/{incident_id}/resolve", headers=headers)
        assert r2.status_code == 409


def test_resolving_directly_from_open_records_the_resolver_as_the_acknowledger(admin_key):
    # resolve_incident allows OPEN -> RESOLVED directly (no acknowledge/
    # investigate step is required) -- when that happens, whoever resolved
    # it should be recorded as the acknowledged_by, since nobody else ever
    # was.
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        incident_id = _open_an_incident(client, headers)
        r = client.post(f"/api/incidents/{incident_id}/resolve", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"
        assert r.json()["acknowledged_by"] is not None
