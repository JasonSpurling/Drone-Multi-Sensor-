from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import list_zones
from app.models import Zone

router = APIRouter()


@router.get("/zones", response_model=list[Zone])
def get_zones(
    principal: Principal = Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)),
) -> list[Zone]:
    return list_zones(site_id=principal.site_id, active_only=True)
