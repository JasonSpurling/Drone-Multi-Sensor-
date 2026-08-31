"""GET /api/me -- who the current request is authenticated as, regardless
of whether that's an API key or an SSO session cookie. Used by
dashboard.html to show "logged in as X" and an SSO-only "Log out" link.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def viewer_key(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"viewer-key": {"role": "viewer", "label": "Alice"}}))
    return "viewer-key"


@pytest.fixture
def ingest_key(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"ingest-key": "ingest"}))
    return "ingest-key"


def test_me_returns_the_authenticated_principals_name_role_and_site(viewer_key):
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-API-Key": viewer_key})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Alice"
        assert body["role"] == "viewer"
        assert body["site_id"] is not None


def test_me_falls_back_to_role_as_the_name_when_the_key_has_no_label(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"admin-key": "admin"}))
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-API-Key": "admin-key"})
        assert r.status_code == 200
        assert r.json()["name"] == "admin"


def test_me_rejects_an_ingest_only_key(ingest_key):
    # ingest is deliberately not in _viewer_roles -- a sensor key posting
    # detections has no business asking who it's authenticated as via a
    # dashboard-facing endpoint.
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-API-Key": ingest_key})
        assert r.status_code == 403


def test_me_requires_authentication_when_keys_are_configured(viewer_key):
    with TestClient(app) as client:
        r = client.get("/api/me")
        assert r.status_code == 401


def test_me_open_access_when_no_keys_configured(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")
    with TestClient(app) as client:
        r = client.get("/api/me")
        assert r.status_code == 200
        assert r.json()["role"] == "admin"
