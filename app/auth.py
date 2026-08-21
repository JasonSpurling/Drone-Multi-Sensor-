"""API key authentication with per-key roles (RBAC) and per-key site
scoping.

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
    """The authenticated caller: which role's key it presented, and which
    site that key is scoped to (see app/sites.py -- every request acts on
    exactly one site's data; there is no cross-site key in this version).
    `name` is what gets recorded as e.g. an incident's acknowledged_by --
    never the raw key.
    """

    def __init__(self, role: str, site_id: int) -> None:
        self.role = role
        self.site_id = site_id
        self.name = role

    def __repr__(self) -> str:  # pragma: no cover
        return f"Principal(role={self.role!r}, site_id={self.site_id!r})"


def _configured_keys() -> dict[str, dict]:
    """key -> {"role": ..., "site": site-name-or-None}, merging the legacy
    single DRONE_API_KEY (role=admin, no site override -> default site)
    with DRONE_API_KEYS, if set. DRONE_API_KEYS' JSON value for a key can
    be either a bare role string (the pre-multi-site format -- kept
    working, resolves to the default site) or an object
    {"role": ..., "site": "site-name"} to scope that key to a specific
    non-default site. Read fresh each call (cheap; a handful of entries)
    so config changes -- including in tests -- take effect immediately.
    """
    keys: dict[str, dict] = {}
    if config.API_KEY:
        keys[config.API_KEY] = {"role": ROLE_ADMIN, "site": None}
    if config.API_KEYS_JSON:
        for key, value in json.loads(config.API_KEYS_JSON).items():
            if isinstance(value, str):
                keys[key] = {"role": value, "site": None}
            else:
                keys[key] = {"role": value["role"], "site": value.get("site")}
    return keys


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


def require_role(*roles: str):
    """FastAPI dependency factory: the caller's key must map to a role that
    satisfies at least one of `roles`.
    """
    required = set(roles)

    def dependency(x_api_key: str | None = Header(default=None)) -> Principal:
        keys = _configured_keys()
        if not keys:
            from app.sites import ensure_default_site

            return Principal(role=ROLE_ADMIN, site_id=ensure_default_site())

        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Missing X-API-Key header")

        matched = next(
            (entry for key, entry in keys.items() if secrets.compare_digest(x_api_key, key)), None
        )
        if matched is None:
            raise HTTPException(status_code=401, detail="Invalid X-API-Key header")

        matched_role = matched["role"]
        if not (_ROLE_IMPLIES.get(matched_role, {matched_role}) & required):
            raise HTTPException(
                status_code=403, detail=f"Role '{matched_role}' cannot access this endpoint"
            )
        return Principal(role=matched_role, site_id=_resolve_site_id(matched["site"]))

    return dependency
