"""The actual OIDC login flow (GET /auth/login -> redirect to the IdP ->
GET /auth/callback -> a drone_session cookie) run end-to-end against a
real (if minimal) local OpenID Connect provider (tests/_stub_oidc_provider.py)
-- not authlib internals mocked out, a genuine authorization-code + PKCE
exchange and RS256 ID token signature verification, the same code path a
real Okta/Auth0/Azure AD login would exercise.

TestClient routes every request through the app's own ASGI transport
regardless of which host the URL names (it has no real network path out),
so the hop to the stub IdP's /authorize has to go through a real httpx
client instead -- only the calls to this app's own /auth/login and
/auth/callback go through TestClient.
"""

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.oidc as oidc_module
from app.main import app
from app.sso_session import SESSION_COOKIE_NAME, verify_session_token
from tests._stub_oidc_provider import StubOidcProvider


def _configure_oidc(monkeypatch, idp: StubOidcProvider, **overrides) -> None:
    monkeypatch.setattr("app.config.OIDC_ISSUER_URL", idp.issuer_url)
    monkeypatch.setattr("app.config.OIDC_CLIENT_ID", "test-client")
    monkeypatch.setattr("app.config.OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("app.config.OIDC_SESSION_SECRET", Fernet.generate_key().decode())
    for key, value in overrides.items():
        monkeypatch.setattr(f"app.config.{key}", value)
    oidc_module.reset_for_tests()


def _run_login(client: TestClient) -> httpx.Response:
    """Drives the real redirect chain: our app's /auth/login -> the real
    stub IdP's /authorize (a genuine network hop) -> back to our app's
    /auth/callback (via TestClient again, so it shares state/nonce
    cookies with the /auth/login call). Returns the final response.
    """
    login_response = client.get("/auth/login", follow_redirects=False)
    assert login_response.status_code in (302, 307), login_response.text
    authorize_url = login_response.headers["location"]

    with httpx.Client(follow_redirects=False) as idp_client:
        authorize_response = idp_client.get(authorize_url)
    assert authorize_response.status_code == 302, authorize_response.text
    callback_url = authorize_response.headers["location"]

    return client.get(callback_url, follow_redirects=True)


def test_login_route_404s_when_oidc_is_not_configured():
    with TestClient(app) as client:
        response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 404


def test_callback_route_404s_when_oidc_is_not_configured():
    with TestClient(app) as client:
        response = client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 404


def test_full_login_round_trip_sets_a_valid_session_cookie(monkeypatch):
    with StubOidcProvider() as idp:
        _configure_oidc(monkeypatch, idp)
        idp.next_claims = {"sub": "user-123", "email": "alice@example.com", "role": "operator"}

        with TestClient(app) as client:
            final = _run_login(client)
            cookie = client.cookies.get(SESSION_COOKIE_NAME)

        assert final.status_code == 200  # landed on the dashboard ("/") after the callback's redirect
        assert cookie is not None
        claims = verify_session_token(cookie)
        assert claims == {"role": "operator", "site": None, "label": "alice@example.com"}


def test_login_round_trip_falls_back_to_the_default_role_for_an_unrecognized_role_claim(monkeypatch):
    with StubOidcProvider() as idp:
        _configure_oidc(monkeypatch, idp, OIDC_DEFAULT_ROLE="viewer")
        idp.next_claims = {"sub": "user-456", "email": "bob@example.com", "role": "super-admin"}

        with TestClient(app) as client:
            _run_login(client)
            claims = verify_session_token(client.cookies.get(SESSION_COOKIE_NAME))

        assert claims["role"] == "viewer"  # not "super-admin" -- not a role this app recognizes


def test_login_round_trip_maps_a_site_claim(monkeypatch):
    with StubOidcProvider() as idp:
        _configure_oidc(monkeypatch, idp)
        idp.next_claims = {
            "sub": "user-789", "email": "carol@example.com", "role": "admin", "site": "warehouse-north",
        }

        with TestClient(app) as client:
            _run_login(client)
            claims = verify_session_token(client.cookies.get(SESSION_COOKIE_NAME))

        assert claims == {"role": "admin", "site": "warehouse-north", "label": "carol@example.com"}


def test_a_forged_id_token_signature_is_rejected(monkeypatch):
    """The whole point of using a real ID-token verifier (authlib), not
    hand-rolled JSON parsing: a token signed by a DIFFERENT key than the
    one the configured issuer's own JWKS advertises must not be trusted.
    """
    with StubOidcProvider() as legit_idp, StubOidcProvider() as attacker_idp:
        _configure_oidc(monkeypatch, legit_idp)
        # The token that comes back is signed by attacker_idp's key (and
        # carries attacker_idp's issuer) even though the app is configured
        # to trust legit_idp -- simulates a token an attacker crafted with
        # a key the configured issuer never published.
        attacker_idp.next_claims = {"sub": "user-123", "email": "mallory@example.com", "role": "admin"}

        with TestClient(app) as client:
            login_response = client.get("/auth/login", follow_redirects=False)
            authorize_url = login_response.headers["location"].replace(
                legit_idp.issuer_url, attacker_idp.issuer_url
            )
            with httpx.Client(follow_redirects=False) as idp_client:
                authorize_response = idp_client.get(authorize_url)
            callback_url = authorize_response.headers["location"]
            final = client.get(callback_url, follow_redirects=True)

        # authlib's ID-token verification against legit_idp's JWKS must
        # fail (wrong signing key, and the wrong issuer besides) -- either
        # way, no session cookie gets set.
        assert final.status_code >= 400 or client.cookies.get(SESSION_COOKIE_NAME) is None


def test_logout_clears_the_session_cookie(monkeypatch):
    with StubOidcProvider() as idp:
        _configure_oidc(monkeypatch, idp)
        idp.next_claims = {"sub": "user-123", "email": "alice@example.com", "role": "viewer"}

        with TestClient(app) as client:
            _run_login(client)
            assert client.cookies.get(SESSION_COOKIE_NAME) is not None  # logged in first, for real

            response = client.get("/auth/logout", follow_redirects=False)
            assert response.status_code in (302, 307)
            assert 'drone_session=""' in response.headers["set-cookie"]
            assert "Max-Age=0" in response.headers["set-cookie"]
