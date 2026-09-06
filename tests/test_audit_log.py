"""Admin actions that can change what the system trusts or silence a real
alert (sensor/operator registration, site creation, incident acknowledge/
resolve) must leave a real "who did this, when" record -- see
app/schema.py's audit_log table and app/db.py's record_audit().
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
    monkeypatch.setattr(
        "app.config.API_KEYS_JSON",
        json.dumps({"admin-key": {"role": "admin", "label": "ops-console"}}),
    )
    return "admin-key"


def test_registering_a_sensor_is_recorded_in_the_audit_log(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        r = client.put(
            "/api/sensor-registrations/radar-9",
            json={"sensor_type": "radar", "latitude": 51.5, "longitude": -0.1},
            headers=headers,
        )
        assert r.status_code == 201

        entries = client.get("/api/audit-log", headers=headers).json()
        assert len(entries) == 1
        assert entries[0]["action"] == "sensor.register"
        assert entries[0]["target"] == "radar-9"
        # The configured key's label, not its role and never the raw key.
        assert entries[0]["actor"] == "ops-console"


def test_registering_an_operator_is_recorded_in_the_audit_log(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        r = client.put(
            "/api/authorized-operators/op-1", json={"name": "Test Operator"}, headers=headers
        )
        assert r.status_code == 201

        entries = client.get("/api/audit-log", headers=headers).json()
        assert entries[0]["action"] == "operator.register"
        assert entries[0]["target"] == "op-1"


def test_creating_a_site_is_recorded_with_no_site_id(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        r = client.post("/api/sites", json={"name": "warehouse-north"}, headers=headers)
        assert r.status_code == 201

        entries = client.get("/api/audit-log", headers=headers).json()
        assert entries[0]["action"] == "site.create"
        assert entries[0]["target"] == "warehouse-north"
        assert entries[0]["site_id"] is None


def test_acknowledging_and_resolving_an_incident_are_both_recorded(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        client.post("/api/detections", json=DETECTION_BODY, headers=headers)
        # Land a second detection inside the seeded restricted zone so an
        # incident actually opens to acknowledge/resolve.
        zone_body = {**DETECTION_BODY, "latitude": 51.5, "longitude": -0.10}
        client.post("/api/detections", json=zone_body, headers=headers)
        incidents = client.get("/api/incidents", headers=headers).json()
        assert incidents, "expected the zone-incursion detection to open an incident"
        incident_id = incidents[0]["id"]

        client.post(f"/api/incidents/{incident_id}/acknowledge", headers=headers)
        client.post(f"/api/incidents/{incident_id}/resolve", headers=headers)

        entries = client.get("/api/audit-log", headers=headers).json()
        actions = [e["action"] for e in entries]
        assert "incident.acknowledge" in actions
        assert "incident.resolve" in actions


def test_visual_verification_is_recorded_with_result_and_note(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        track_id = client.post("/api/detections", json=DETECTION_BODY, headers=headers).json()["track_id"]

        r = client.post(
            f"/api/tracks/{track_id}/verify-visual",
            json={"result": "confirmed", "note": "Matches the radar track, visible quadcopter"},
            headers=headers,
        )
        assert r.status_code == 204

        entries = client.get("/api/audit-log", headers=headers).json()
        assert entries[0]["action"] == "track.visual_verify"
        assert entries[0]["target"] == str(track_id)
        assert entries[0]["detail"] == "confirmed: Matches the radar track, visible quadcopter"
        # Never touches the track's own classification/risk fields -- this
        # is a person's own conclusion, not a new automated signal.
        track = client.get(f"/api/tracks/{track_id}", headers=headers).json()
        assert track["classification"] == "drone"


def test_visual_verification_requires_operator_or_admin_role(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"view-key": "viewer"}))
    with TestClient(app) as client:
        r = client.post(
            "/api/tracks/1/verify-visual",
            json={"result": "confirmed"},
            headers={"X-API-Key": "view-key"},
        )
        assert r.status_code == 403


def test_audit_log_requires_admin_role(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"view-key": "viewer"}))
    with TestClient(app) as client:
        r = client.get("/api/audit-log", headers={"X-API-Key": "view-key"})
        assert r.status_code == 403
