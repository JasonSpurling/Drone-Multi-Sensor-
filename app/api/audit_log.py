"""GET /api/audit-log -- who did what admin action, when (see
app/schema.py's audit_log table and app/db.py's record_audit()).
"""

from fastapi import APIRouter, Depends, Query

from app.auth import ROLE_ADMIN, require_role
from app.db import list_audit_log
from app.models import AuditLogEntry

router = APIRouter()


@router.get(
    "/audit-log",
    response_model=list[AuditLogEntry],
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def get_audit_log(
    limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0)
) -> list[AuditLogEntry]:
    # Deployment-wide, not scoped to the caller's own site -- see
    # app/db.py's list_audit_log() docstring for why (same reasoning as
    # app/api/sites.py's endpoints).
    return list_audit_log(limit=limit, offset=offset)
