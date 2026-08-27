"""The session cookie app.oidc's /auth/callback route issues after a
successful OIDC login, and app.auth verifies on every subsequent
authenticated request.

Deliberately has zero OIDC-specific dependencies: it's sealed with
Fernet (symmetric encrypt-then-MAC, from `cryptography`, already a hard
dependency of this app -- see app/remote_id.py's Ed25519 detection
signing) rather than a real JWT, and Fernet's own built-in TTL check
(the `ttl=` argument to
`decrypt()`) handles expiry without this needing to parse/compare
timestamps itself. That split matters: verifying a session (this module)
runs on every authenticated request and must never need `authlib`
installed, while only *creating* one (app/oidc.py's callback handler,
run once per login) needs that OIDC-specific, optional dependency.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from app import config

SESSION_COOKIE_NAME = "drone_session"


def _fernet() -> Fernet:
    secret = config.OIDC_SESSION_SECRET
    if not secret:
        raise RuntimeError(
            "DRONE_OIDC_SESSION_SECRET is not set -- required to create or verify "
            "an SSO session cookie. Generate one with: python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(secret.encode())
    except ValueError as exc:
        raise RuntimeError(
            "DRONE_OIDC_SESSION_SECRET is not a valid Fernet key (must be 32 "
            "url-safe base64-encoded bytes) -- generate one with: python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def create_session_token(*, role: str, site: str | None, label: str | None) -> str:
    """Seals `role`/`site` (a site *name*, resolved to a site_id at verify
    time via the same lookup DRONE_API_KEYS' "site" field uses -- see
    app.auth._resolve_site_id) and `label` (what shows up as the actor in
    the audit log / GET /api/admin/keys, same as an API key's own label)
    into an opaque token suitable for a cookie value.
    """
    payload = json.dumps({"role": role, "site": site, "label": label}).encode()
    return _fernet().encrypt(payload).decode()


def verify_session_token(token: str) -> dict | None:
    """None for anything invalid -- tampered, wrong key, malformed, or
    older than DRONE_OIDC_SESSION_MAX_AGE_SECONDS -- never raises, so a
    caller can treat this exactly like "no session cookie sent" and fall
    through to whatever other auth path exists, rather than 500ing a
    request over a stale/forged cookie.
    """
    try:
        payload = _fernet().decrypt(token.encode(), ttl=int(config.OIDC_SESSION_MAX_AGE_SECONDS))
    except (InvalidToken, ValueError):
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None
