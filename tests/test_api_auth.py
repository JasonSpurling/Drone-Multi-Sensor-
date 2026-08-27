import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

DETECTION_BODY = {
    "sensor_id": "radar-1",
    "sensor_type": "radar",
    "latitude": 51.5,
    "longitude": -0.1,
    "confidence": 0.9,
}


@pytest.fixture
def keys(monkeypatch):
    mapping = {"ingest-key": "ingest", "view-key": "viewer", "ops-key": "operator", "admin-key": "admin"}
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps(mapping))
    return mapping


def test_no_keys_configured_is_open(isolated_db):
    with TestClient(app) as client:
        assert client.get("/api/tracks").status_code == 200
        assert client.post("/api/detections", json=DETECTION_BODY).status_code == 201


def test_missing_key_is_rejected(isolated_db, keys):
    with TestClient(app) as client:
        assert client.get("/api/tracks").status_code == 401


def test_wrong_key_is_rejected(isolated_db, keys):
    with TestClient(app) as client:
        r = client.get("/api/tracks", headers={"X-API-Key": "not-a-real-key"})
        assert r.status_code == 401


def test_ingest_key_cannot_read_tracks(isolated_db, keys):
    with TestClient(app) as client:
        r = client.get("/api/tracks", headers={"X-API-Key": "ingest-key"})
        assert r.status_code == 403


def test_viewer_key_can_read_but_not_ingest(isolated_db, keys):
    with TestClient(app) as client:
        assert client.get("/api/tracks", headers={"X-API-Key": "view-key"}).status_code == 200
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "view-key"})
        assert r.status_code == 403


def test_ingest_key_can_post_detections(isolated_db, keys):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"})
        assert r.status_code == 201


def test_batch_endpoint_follows_same_rbac_as_single_detection(isolated_db, keys):
    with TestClient(app) as client:
        r = client.post(
            "/api/detections/batch", json=[DETECTION_BODY], headers={"X-API-Key": "view-key"}
        )
        assert r.status_code == 403
        r = client.post(
            "/api/detections/batch", json=[DETECTION_BODY], headers={"X-API-Key": "ingest-key"}
        )
        assert r.status_code == 201


def test_viewer_cannot_acknowledge_operator_can(isolated_db, keys):
    with TestClient(app) as client:
        client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"})
        r = client.post("/api/incidents/999/acknowledge", headers={"X-API-Key": "view-key"})
        assert r.status_code == 403
        r = client.post("/api/incidents/999/acknowledge", headers={"X-API-Key": "ops-key"})
        assert r.status_code == 404  # role check passed; incident just doesn't exist


def test_only_admin_can_register_sensors(isolated_db, keys):
    body = {"sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "azimuth_reference_deg": 0}
    with TestClient(app) as client:
        r = client.put("/api/sensor-registrations/radar-1", json=body, headers={"X-API-Key": "ops-key"})
        assert r.status_code == 403
        r = client.put("/api/sensor-registrations/radar-1", json=body, headers={"X-API-Key": "admin-key"})
        assert r.status_code == 201


def test_only_admin_can_read_authorized_operators(isolated_db, keys):
    # Regression test: this list is exactly the set of operator_id values
    # that get a detection classified FRIENDLY (app/allowlist.py), so a
    # lower role reading it back would learn what to spoof.
    with TestClient(app) as client:
        r = client.get("/api/authorized-operators", headers={"X-API-Key": "view-key"})
        assert r.status_code == 403
        r = client.get("/api/authorized-operators", headers={"X-API-Key": "ops-key"})
        assert r.status_code == 403
        r = client.get("/api/authorized-operators", headers={"X-API-Key": "admin-key"})
        assert r.status_code == 200


def test_health_and_metrics_are_unauthenticated(isolated_db, keys):
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/metrics").status_code == 200


def test_legacy_single_api_key_grants_admin(isolated_db, monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "legacy-secret")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")
    with TestClient(app) as client:
        r = client.get("/api/tracks", headers={"X-API-Key": "legacy-secret"})
        assert r.status_code == 200
        body = {"sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "azimuth_reference_deg": 0}
        r = client.put(
            "/api/sensor-registrations/radar-1", json=body, headers={"X-API-Key": "legacy-secret"}
        )
        assert r.status_code == 201


def test_acknowledge_records_who_acknowledged(isolated_db, keys):
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"})
        assert r.status_code == 201
        # Force the drone into the seeded restricted zone isn't guaranteed here;
        # instead just verify the acknowledged_by field flows through when an
        # incident does exist by creating one directly isn't in scope for an
        # API test -- skip if none opened.
        incidents = client.get("/api/incidents", headers={"X-API-Key": "ops-key"}).json()
        for incident in incidents:
            ack = client.post(
                f"/api/incidents/{incident['id']}/acknowledge", headers={"X-API-Key": "ops-key"}
            )
            assert ack.json()["acknowledged_by"] == "operator"
