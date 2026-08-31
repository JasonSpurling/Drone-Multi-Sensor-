"""GET /api/me -- who the current request is authenticated as, regardless
of which credential it used (X-API-Key header or an SSO session cookie --
see app/auth.py). Nothing before this endpoint let a client ask that
question; a dashboard showing "logged in as X" or a "Log out" link (only
meaningful for an SSO session, not a static API key) had no way to know
who X even is.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role

router = APIRouter()


@router.get("/me")
def get_me(principal: Principal = Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN))) -> dict:
    return {"name": principal.name, "role": principal.role, "site_id": principal.site_id}
