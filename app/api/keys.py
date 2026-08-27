"""GET /api/admin/keys -- every configured key's non-secret metadata
(label, role, site, revoked/expiry) plus its recorded usage, so an admin
can actually see which keys are stale/unused without grepping config and
guessing. Never returns a raw key -- see app/auth.py's hash_key().
"""

from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, configured_keys, hash_key, require_role
from app.db import get_key_usage
from app.models import ApiKeyStatus

router = APIRouter()


@router.get(
    "/admin/keys",
    response_model=list[ApiKeyStatus],
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def get_admin_keys() -> list[ApiKeyStatus]:
    keys = configured_keys()
    usage = get_key_usage([hash_key(raw_key) for raw_key in keys])
    statuses = []
    for raw_key, entry in keys.items():
        last_used_at, use_count = usage.get(hash_key(raw_key), (None, 0))
        statuses.append(
            ApiKeyStatus(
                label=entry["label"] or entry["role"],
                role=entry["role"],
                site=entry["site"],
                revoked=entry["revoked"],
                expires_at=entry["expires_at"],
                last_used_at=last_used_at,
                use_count=use_count,
            )
        )
    return statuses
