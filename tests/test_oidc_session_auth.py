"""app.auth's SSO-session-cookie path (_try_session_cookie, wired into
require_role) -- exercised through the real API via TestClient, not by
calling the dependency function directly, so this proves the actual
request path (cookie parsing, role/site resolution, fallback to the
normal X-API-Key path) works end-to-end. No OIDC provider needed here --
the cookie itself is created directly via app.sso_session, exactly the
shape app/api/auth_sso.py's /auth/callback would produce (see
tests/test_oidc_login.py for the full browser-redirect flow against a
local stub provider).
"""

import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db import create_site
from app.main import app
from app.sso_session import SESSION_COOKIE_NAME, create_session_token

API_KEY = "some-configured-key-0123456789ab"


def _enable_oidc(monkeypatch):
    monkeypatch.setattr("app.config.OIDC_ISSUER_URL", "https://idp.example.com")
    monkeypatch.setattr("app.config.OIDC_CLIENT_ID", "test-client")
    monkeypatch.setattr("app.config.OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("app.config.OIDC_SESSION_SECRET", Fernet.generate_key().decode())


def test_a_valid_session_cookie_authenticates_with_no_api_key_configured(monkeypatch):
    _enable_oidc(monkeypatch)
    token = create_session_token(role="viewer", site=None, label="alice@example.com")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get("/api/tracks")
    assert response.status_code == 200


def test_a_valid_session_cookie_authenticates_when_api_keys_are_configured(monkeypatch, isolated_db):
    _enable_oidc(monkeypatch)
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({API_KEY: "admin"}))
    monkeypatch.setattr("app.config.API_KEY", "")
    token = create_session_token(role="viewer", site=None, label="alice@example.com")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get("/api/tracks")
    assert response.status_code == 200


def test_session_cookie_is_scoped_to_the_site_it_names(monkeypatch, isolated_db):
    _enable_oidc(monkeypatch)
    site_b = create_site("site-b")
    monkeypatch.setattr(
        "app.config.API_KEYS_JSON",
        json.dumps({API_KEY: {"role": "admin", "site": "site-b"}}),
    )
    monkeypatch.setattr("app.config.API_KEY", "")
    token = create_session_token(role="admin", site="site-b", label=None)

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        # Create a track scoped to site-b via the API key path, then read
        # it back via the session-cookie path -- proves the cookie really
        # resolves to site-b's data, not the default site's.
        detection = client.post(
            "/api/detections",
            json={
                "sensor_id": "radar-1", "sensor_type": "radar",
                "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
            },
            headers={"X-API-Key": API_KEY},
        ).json()
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get(f"/api/tracks/{detection['track_id']}")
    assert response.status_code == 200
    assert response.json()["site_id"] == site_b.id


def test_a_session_cookie_with_an_insufficient_role_is_rejected_with_403(monkeypatch):
    _enable_oidc(monkeypatch)
    token = create_session_token(role="ingest", site=None, label=None)

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get("/api/tracks")  # requires viewer, ingest doesn't imply it
    assert response.status_code == 403


def test_a_tampered_session_cookie_falls_through_to_the_normal_api_key_path(monkeypatch, isolated_db):
    _enable_oidc(monkeypatch)
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({API_KEY: "admin"}))
    monkeypatch.setattr("app.config.API_KEY", "")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
        response = client.get("/api/tracks")
    assert response.status_code == 401  # falls through to "missing X-API-Key", not a 500


def test_session_cookie_is_ignored_when_oidc_is_not_configured(monkeypatch, isolated_db):
    # OIDC left disabled (default) -- a session cookie must be inert, even
    # a well-formed one, so this doesn't become a backdoor around
    # DRONE_API_KEYS on a deployment that never opted into SSO.
    monkeypatch.setattr("app.config.OIDC_SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({API_KEY: "admin"}))
    monkeypatch.setattr("app.config.API_KEY", "")
    token = create_session_token(role="admin", site=None, label=None)

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get("/api/tracks")
    assert response.status_code == 401


def test_x_api_key_header_takes_priority_over_a_session_cookie(monkeypatch, isolated_db):
    _enable_oidc(monkeypatch)
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({API_KEY: "viewer"}))
    monkeypatch.setattr("app.config.API_KEY", "")
    # A cookie for a role that WOULD be rejected -- if the header weren't
    # actually taking priority, this request would 403 instead of 200.
    token = create_session_token(role="ingest", site=None, label=None)

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get("/api/tracks", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
