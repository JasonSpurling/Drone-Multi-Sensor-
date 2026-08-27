"""Generic OpenID Connect login (app/api/auth_sso.py's routes) -- works
with any standards-compliant OIDC provider via issuer discovery
(DRONE_OIDC_ISSUER_URL's /.well-known/openid-configuration), not
vendor-specific code for a particular IdP.

`authlib` is only imported inside get_oauth_client() (not at this
module's own import time, and never from app.auth -- see
app/sso_session.py's docstring for why that split matters), so a
deployment that never sets DRONE_OIDC_ISSUER_URL/_CLIENT_ID/_CLIENT_SECRET
never needs it installed (it's in requirements-oidc.txt, not
requirements.txt).
"""

from __future__ import annotations

from typing import Any

from app import config


def oidc_enabled() -> bool:
    return bool(config.OIDC_ISSUER_URL and config.OIDC_CLIENT_ID and config.OIDC_CLIENT_SECRET)


_oauth_registry: Any = None


def get_oauth_client() -> Any:
    """The registered authlib OIDC client -- constructed once (issuer
    discovery is an HTTP round trip) and cached at module level, the same
    way a real deployment would construct it once at startup rather than
    per-request. Read config live (not captured at import time) so a
    test's monkeypatch of DRONE_OIDC_* still takes effect; only the
    *client itself* is cached, invalidated by reset_for_tests() below.
    """
    global _oauth_registry
    if _oauth_registry is None:
        from authlib.integrations.starlette_client import OAuth

        oauth = OAuth()
        oauth.register(
            name="drone_oidc",
            client_id=config.OIDC_CLIENT_ID,
            client_secret=config.OIDC_CLIENT_SECRET,
            server_metadata_url=f"{config.OIDC_ISSUER_URL.rstrip('/')}/.well-known/openid-configuration",
            client_kwargs={"scope": "openid profile email"},
        )
        _oauth_registry = oauth.drone_oidc
    return _oauth_registry


def reset_for_tests() -> None:
    """Without this, a test pointing DRONE_OIDC_ISSUER_URL at its own
    local stub provider would see the *previous* test's cached client
    (registered against a different, by-then-torn-down issuer) instead of
    discovering the new one.
    """
    global _oauth_registry
    _oauth_registry = None
