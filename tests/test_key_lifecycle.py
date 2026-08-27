"""Expiry, revocation, and usage tracking for API keys -- see
app/auth.py's configured_keys()/record_key_usage() and the
GET /api/admin/keys endpoint (app/api/keys.py).
"""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.util import utcnow

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


@pytest.fixture
def admin_key(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"admin-key": "admin"}))
    return "admin-key"


def test_revoked_key_is_rejected(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr(
        "app.config.API_KEYS_JSON", json.dumps({"ingest-key": {"role": "ingest", "revoked": True}})
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"}
        )
        assert r.status_code == 401
        assert "revoked" in r.json()["detail"].lower()


def test_expired_key_is_rejected(monkeypatch):
    expired_at = (utcnow() - timedelta(days=1)).isoformat()
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr(
        "app.config.API_KEYS_JSON",
        json.dumps({"ingest-key": {"role": "ingest", "expires_at": expired_at}}),
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"}
        )
        assert r.status_code == 401
        assert "expired" in r.json()["detail"].lower()


def test_not_yet_expired_key_still_works(monkeypatch):
    future = (utcnow() + timedelta(days=1)).isoformat()
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr(
        "app.config.API_KEYS_JSON",
        json.dumps({"ingest-key": {"role": "ingest", "expires_at": future}}),
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "ingest-key"}
        )
        assert r.status_code == 201


def test_admin_keys_endpoint_lists_metadata_without_the_raw_key(admin_key):
    with TestClient(app) as client:
        r = client.get("/api/admin/keys", headers={"X-API-Key": admin_key})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["role"] == "admin"
        assert "admin-key" not in json.dumps(body)  # the raw key never appears in the response


def test_admin_keys_endpoint_reports_usage_after_a_request(monkeypatch):
    from app.auth import reset_usage_throttle_for_tests

    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"admin-key": "admin"}))
    reset_usage_throttle_for_tests()
    with TestClient(app) as client:
        headers = {"X-API-Key": "admin-key"}
        client.get("/api/tracks", headers=headers)  # first authenticated call -- records usage

        body = client.get("/api/admin/keys", headers=headers).json()
        assert body[0]["use_count"] >= 1
        assert body[0]["last_used_at"] is not None


def test_admin_keys_endpoint_requires_admin_role(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"view-key": "viewer"}))
    with TestClient(app) as client:
        r = client.get("/api/admin/keys", headers={"X-API-Key": "view-key"})
        assert r.status_code == 403
