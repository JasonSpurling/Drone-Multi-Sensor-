"""app.sso_session: the signed+encrypted session cookie app.oidc's
/auth/callback issues after a successful OIDC login, and app.auth
verifies on every subsequent authenticated request. No OIDC provider
involved here -- pure round-trip/tamper/expiry tests of the cookie
mechanism itself (see tests/test_oidc_login.py for the full login flow
against a local stub provider).
"""

import time

import pytest
from cryptography.fernet import Fernet

import app.sso_session as sso_session


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    monkeypatch.setattr(sso_session.config, "OIDC_SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setattr(sso_session.config, "OIDC_SESSION_MAX_AGE_SECONDS", 28800.0)


def test_create_and_verify_round_trips():
    token = sso_session.create_session_token(role="operator", site="warehouse-north", label="alice@example.com")
    claims = sso_session.verify_session_token(token)
    assert claims == {"role": "operator", "site": "warehouse-north", "label": "alice@example.com"}


def test_verify_returns_none_for_garbage_input():
    assert sso_session.verify_session_token("not-a-real-token") is None


def test_verify_returns_none_for_a_token_signed_with_a_different_secret():
    token = sso_session.create_session_token(role="admin", site=None, label=None)
    # A different secret entirely -- simulates a forged/stale cookie.
    other = Fernet.generate_key().decode()
    import app.config as config

    original = config.OIDC_SESSION_SECRET
    config.OIDC_SESSION_SECRET = other
    try:
        assert sso_session.verify_session_token(token) is None
    finally:
        config.OIDC_SESSION_SECRET = original


def test_verify_returns_none_for_an_expired_token(monkeypatch):
    # Fernet's own TTL check has 1-second resolution (its embedded
    # timestamp is whole Unix seconds), so a sub-second max-age can't be
    # tested reliably -- 1s + a bit of margin is the smallest honest gap.
    monkeypatch.setattr(sso_session.config, "OIDC_SESSION_MAX_AGE_SECONDS", 1.0)
    token = sso_session.create_session_token(role="viewer", site=None, label=None)
    time.sleep(2.1)
    assert sso_session.verify_session_token(token) is None


def test_create_session_token_raises_a_clear_error_with_no_secret_configured(monkeypatch):
    monkeypatch.setattr(sso_session.config, "OIDC_SESSION_SECRET", "")
    with pytest.raises(RuntimeError, match="DRONE_OIDC_SESSION_SECRET"):
        sso_session.create_session_token(role="viewer", site=None, label=None)


def test_create_session_token_raises_a_clear_error_for_a_malformed_secret(monkeypatch):
    monkeypatch.setattr(sso_session.config, "OIDC_SESSION_SECRET", "not-a-valid-fernet-key")
    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        sso_session.create_session_token(role="viewer", site=None, label=None)


def test_verify_returns_none_for_a_validly_sealed_but_non_json_payload():
    # A token this app itself never creates -- but decrypt()+ttl succeeding
    # doesn't guarantee the plaintext is JSON, so this path must fail
    # closed (None) rather than let json.JSONDecodeError escape.
    fernet = Fernet(sso_session.config.OIDC_SESSION_SECRET.encode())
    token = fernet.encrypt(b"not valid json{{{").decode()
    assert sso_session.verify_session_token(token) is None
