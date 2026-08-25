"""SSO login routes (GET /auth/login, /auth/callback, /auth/logout) -- see
app/oidc.py for the OIDC mechanism itself and app/sso_session.py for the
session cookie this issues. Mounted without the /api prefix (this is a
browser redirect flow, not a JSON API) and every route 404s unless
DRONE_OIDC_* is configured, so a deployment that hasn't opted into SSO
gets no new attack surface here -- not even routes that exist but reject.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import config
from app.auth import ROLE_ADMIN, ROLE_INGEST, ROLE_OPERATOR, ROLE_VIEWER
from app.oidc import get_oauth_client, oidc_enabled
from app.sso_session import SESSION_COOKIE_NAME, create_session_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["sso"])

_VALID_ROLES = {ROLE_INGEST, ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN}


def _require_oidc_enabled() -> None:
    if not oidc_enabled():
        raise HTTPException(status_code=404, detail="OIDC SSO is not configured")


@router.get("/login", include_in_schema=False)
async def login(request: Request):
    _require_oidc_enabled()
    client = get_oauth_client()
    redirect_uri = config.OIDC_REDIRECT_URL or str(request.url_for("auth_callback"))
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/callback", include_in_schema=False, name="auth_callback")
async def callback(request: Request):
    _require_oidc_enabled()
    client = get_oauth_client()
    from authlib.integrations.base_client.errors import OAuthError

    try:
        token = await client.authorize_access_token(request)
    except OAuthError as exc:
        # A failed/rejected exchange (state mismatch, an IdP error
        # response, a code that doesn't belong to the configured issuer,
        # an ID token that fails signature/issuer/audience/nonce
        # verification, ...) is a rejected login attempt, not a server
        # bug -- a clean 400 here rather than an unhandled 500 traceback.
        logger.warning("OIDC callback rejected: %s", exc)
        raise HTTPException(status_code=400, detail="OIDC login failed") from exc
    # authlib validates the ID token's signature (against the issuer's
    # JWKS), issuer, audience, and nonce as part of authorize_access_token
    # -- `userinfo` here is already-verified claims, not raw untrusted JSON.
    claims = token.get("userinfo") or {}

    role = claims.get(config.OIDC_ROLE_CLAIM)
    if role is not None and role not in _VALID_ROLES:
        logger.warning(
            "OIDC claim '%s'=%r is not a recognized role (ingest/viewer/operator/admin) "
            "-- falling back to DRONE_OIDC_DEFAULT_ROLE", config.OIDC_ROLE_CLAIM, role,
        )
        role = None
    if role is None:
        role = config.OIDC_DEFAULT_ROLE if config.OIDC_DEFAULT_ROLE in _VALID_ROLES else ROLE_VIEWER

    site_name = claims.get(config.OIDC_SITE_CLAIM)
    label = claims.get("email") or claims.get("preferred_username") or claims.get("sub")

    session_token = create_session_token(role=role, site=site_name, label=label)
    response = RedirectResponse(url="/")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=int(config.OIDC_SESSION_MAX_AGE_SECONDS),
    )
    return response


@router.get("/logout", include_in_schema=False)
async def logout() -> RedirectResponse:
    _require_oidc_enabled()
    response = RedirectResponse(url="/")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
