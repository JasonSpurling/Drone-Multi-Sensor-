"""GET /auth/config -- unauthenticated by design, since a client has to
know whether SSO is even available before it can authenticate at all.
Used by dashboard.html to decide whether to show a "Log in" link into
/auth/login instead of always showing one that would 404 when
DRONE_OIDC_* isn't configured.
"""

from fastapi.testclient import TestClient

import app.oidc as oidc_module
from app.main import app


def test_sso_disabled_by_default(monkeypatch):
    monkeypatch.setattr("app.config.OIDC_ISSUER_URL", "")
    monkeypatch.setattr("app.config.OIDC_CLIENT_ID", "")
    monkeypatch.setattr("app.config.OIDC_CLIENT_SECRET", "")
    oidc_module.reset_for_tests()
    with TestClient(app) as client:
        r = client.get("/auth/config")
        assert r.status_code == 200
        assert r.json() == {"sso_enabled": False}


def test_sso_enabled_once_all_three_oidc_settings_are_configured(monkeypatch):
    monkeypatch.setattr("app.config.OIDC_ISSUER_URL", "https://idp.example.com")
    monkeypatch.setattr("app.config.OIDC_CLIENT_ID", "client-id")
    monkeypatch.setattr("app.config.OIDC_CLIENT_SECRET", "client-secret")
    oidc_module.reset_for_tests()
    with TestClient(app) as client:
        r = client.get("/auth/config")
        assert r.status_code == 200
        assert r.json() == {"sso_enabled": True}


def test_auth_config_requires_no_api_key(monkeypatch):
    # Configuring API keys must not lock this endpoint -- a client with no
    # credentials yet is exactly who needs to ask "is SSO available".
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", '{"some-key": "admin"}')
    with TestClient(app) as client:
        r = client.get("/auth/config")
        assert r.status_code == 200
