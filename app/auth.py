"""API key authentication with per-key roles (RBAC).

Active only when at least one key is configured (DRONE_API_KEY or
DRONE_API_KEYS); with neither set, every request is allowed through
unauthenticated -- fine as long as the app is only bound to 127.0.0.1 (see
README "Security").
"""

from __future__ import annotations

import json
import secrets

from fastapi import Header, HTTPException

from app import config

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
    """The authenticated caller: which role's key it presented. `name` is
    what gets recorded as e.g. an incident's acknowledged_by -- never the
    raw key.
    """

    def __init__(self, role: str) -> None:
        self.role = role
        self.name = role

    def __repr__(self) -> str:  # pragma: no cover
        return f"Principal(role={self.role!r})"


def _configured_keys() -> dict[str, str]:
    """key -> role, merging the legacy single DRONE_API_KEY (role=admin,
    for backwards compatibility) with DRONE_API_KEYS (a JSON key->role
    mapping), if set. Read fresh each call (cheap; a handful of entries)
    so config changes -- including in tests -- take effect immediately.
    """
    keys: dict[str, str] = {}
    if config.API_KEY:
        keys[config.API_KEY] = ROLE_ADMIN
    if config.API_KEYS_JSON:
        keys.update(json.loads(config.API_KEYS_JSON))
    return keys


def require_role(*roles: str):
    """FastAPI dependency factory: the caller's key must map to a role that
    satisfies at least one of `roles`.
    """
    required = set(roles)

    def dependency(x_api_key: str | None = Header(default=None)) -> Principal:
        keys = _configured_keys()
        if not keys:
            return Principal(role=ROLE_ADMIN)

        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Missing X-API-Key header")

        matched_role = next(
            (role for key, role in keys.items() if secrets.compare_digest(x_api_key, key)), None
        )
        if matched_role is None:
            raise HTTPException(status_code=401, detail="Invalid X-API-Key header")

        if not (_ROLE_IMPLIES.get(matched_role, {matched_role}) & required):
            raise HTTPException(
                status_code=403, detail=f"Role '{matched_role}' cannot access this endpoint"
            )
        return Principal(role=matched_role)

    return dependency
