"""API key authentication with per-key roles (RBAC) and per-key site
scoping.

Active only when at least one key is configured (DRONE_API_KEY or
DRONE_API_KEYS); with neither set, every request is allowed through
unauthenticated -- fine as long as the app is only bound to 127.0.0.1 (see
README "Security").
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from datetime import datetime

from fastapi import Header, HTTPException

from app import config
from app.util import utcnow

ROLE_INGEST = "ingest"
ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"

# Each role satisfies its own requirement plus whatever's listed here.
# ADMIN satisfies everything; OPERATOR also satisfies VIEWER (an operator
# can do anything a viewer can); INGEST and VIEWER stand alone otherwise --
# an ingest-only sensor key can't read tracks/incidents, deliberately.
_ROLE_IMPLIES = {
    ROLE_ADMIN: {ROLE_INGEST, ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN},
    ROLE_OPERATOR: {ROLE_VIEWER, ROLE_OPERATOR},
    ROLE_VIEWER: {ROLE_VIEWER},
    ROLE_INGEST: {ROLE_INGEST},
}


class Principal:
    """The authenticated caller: which role's key it presented, and which
    site that key is scoped to (see app/sites.py -- every request acts on
    exactly one site's data; there is no cross-site key in this version).
    `name` is what gets recorded as e.g. an incident's acknowledged_by or
    an audit_log entry's actor -- never the raw key; it's the key's
    configured label if it has one, else its role.
    """

    def __init__(self, role: str, site_id: int, label: str | None = None) -> None:
        self.role = role
        self.site_id = site_id
        self.name = label or role

    def __repr__(self) -> str:  # pragma: no cover
        return f"Principal(role={self.role!r}, site_id={self.site_id!r}, name={self.name!r})"


def hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw key -- what api_key_usage is keyed by
    (see app/db.py), so usage tracking never persists anything a reader of
    that table could use to impersonate the key. Not for authentication
    itself (that's still a constant-time compare of the raw value in
    `dependency` below) -- purely a stable, one-way identifier for the
    usage table and GET /api/admin/keys.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


def configured_keys() -> dict[str, dict]:
    """key -> {"role": ..., "site": site-name-or-None, "label": ...,
    "revoked": bool, "expires_at": datetime | None}, merging the legacy
    single DRONE_API_KEY (role=admin, no site override -> default site)
    with DRONE_API_KEYS, if set. DRONE_API_KEYS' JSON value for a key can
    be either a bare role string (the pre-multi-site format -- kept
    working, resolves to the default site, unlabeled, never expires) or
    an object {"role": ..., "site": "site-name", "label": "...",
    "expires_at": "2027-01-01T00:00:00", "revoked": true} -- every field
    but "role" optional. Read fresh each call via config.get_api_key()/
    get_api_keys_json() (cheap; a handful of entries) so config changes --
    including in tests, and including a key rotated by updating
    DRONE_API_KEY_FILE/DRONE_API_KEYS_FILE's target file on disk -- take
    effect immediately; there's no separate "reload config" step.
    """
    keys: dict[str, dict] = {}
    api_key = config.get_api_key()
    if api_key:
        keys[api_key] = {
            "role": ROLE_ADMIN, "site": None, "label": None, "revoked": False, "expires_at": None,
        }
    api_keys_json = config.get_api_keys_json()
    if api_keys_json:
        for key, value in json.loads(api_keys_json).items():
            if isinstance(value, str):
                keys[key] = {
                    "role": value, "site": None, "label": None, "revoked": False, "expires_at": None,
                }
            else:
                expires_at = value.get("expires_at")
                keys[key] = {
                    "role": value["role"],
                    "site": value.get("site"),
                    "label": value.get("label"),
                    "revoked": bool(value.get("revoked", False)),
                    "expires_at": datetime.fromisoformat(expires_at) if expires_at else None,
                }
    return keys


# Bounds how often record_key_usage() actually writes to the database for
# a given key -- the function it wraps is called from require_role's
# dependency, i.e. on every authenticated request including every
# detection POST, and a real DB write per request there would add load to
# exactly the hot path this app is most performance-sensitive about (see
# the README's rate-limiting/pool-sizing sections). "Last used within the
# last 30s" is exactly as useful to an admin checking key staleness as
# "last used at this exact millisecond" would be, so nothing real is lost.
_KEY_USAGE_FLUSH_INTERVAL_S = 30.0
_last_flushed_at: dict[str, float] = {}
_last_flushed_lock = threading.Lock()


def record_key_usage(raw_key: str) -> None:
    key_hash = hash_key(raw_key)
    now_monotonic = time.monotonic()
    with _last_flushed_lock:
        last = _last_flushed_at.get(key_hash)
        if last is not None and now_monotonic - last < _KEY_USAGE_FLUSH_INTERVAL_S:
            return
        _last_flushed_at[key_hash] = now_monotonic

    from app.db import record_key_usage as db_record_key_usage

    db_record_key_usage(key_hash, when=utcnow())


def reset_usage_throttle_for_tests() -> None:
    """Without this, a test that authenticates twice for the same key
    (e.g. to assert use_count went from 1 to 2) would see the second call
    silently dropped by the same _KEY_USAGE_FLUSH_INTERVAL_S throttle that
    protects the real detection-ingest hot path from write amplification.
    """
    with _last_flushed_lock:
        _last_flushed_at.clear()


def _resolve_site_id(site_name: str | None) -> int:
    from app.sites import ensure_default_site

    if site_name is None:
        return ensure_default_site()

    from app.db import get_site_by_name

    site = get_site_by_name(site_name)
    if site is None:
        raise HTTPException(status_code=500, detail=f"API key references unknown site '{site_name}'")
    assert site.id is not None
    return site.id


def authenticate_key(raw_key: str | None, required: set[str], *, missing_key_detail: str) -> Principal:
    """The actual authentication/authorization logic behind require_role()
    below, factored out so a context that can't use FastAPI's
    Header()-based dependency injection -- specifically the WebSocket
    endpoint (app/api/live.py), since a browser WebSocket client can't
    set a custom X-API-Key header and instead authenticates via an
    `api_key` query param -- can still run exactly the same checks
    (revocation, expiry, role, usage tracking) rather than a second,
    divergent copy of them.
    """
    keys = configured_keys()
    if not keys:
        from app.sites import ensure_default_site

        return Principal(role=ROLE_ADMIN, site_id=ensure_default_site())

    if raw_key is None:
        raise HTTPException(status_code=401, detail=missing_key_detail)

    matched = next((entry for key, entry in keys.items() if secrets.compare_digest(raw_key, key)), None)
    if matched is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Checked before the role/403 check below -- a revoked or expired key
    # gets 401 regardless of which role it was, the same as an
    # unrecognized key would, rather than leaking through 403 which role
    # it used to have.
    if matched["revoked"]:
        raise HTTPException(status_code=401, detail="API key has been revoked")
    if matched["expires_at"] is not None and utcnow() >= matched["expires_at"]:
        raise HTTPException(status_code=401, detail="API key has expired")

    matched_role = matched["role"]
    if not (_ROLE_IMPLIES.get(matched_role, {matched_role}) & required):
        raise HTTPException(status_code=403, detail=f"Role '{matched_role}' cannot access this endpoint")
    record_key_usage(raw_key)
    return Principal(role=matched_role, site_id=_resolve_site_id(matched["site"]), label=matched["label"])


def require_role(*roles: str):
    """FastAPI dependency factory: the caller's key must map to a role that
    satisfies at least one of `roles`.
    """
    required = set(roles)

    def dependency(x_api_key: str | None = Header(default=None)) -> Principal:
        return authenticate_key(x_api_key, required, missing_key_detail="Missing X-API-Key header")

    return dependency
