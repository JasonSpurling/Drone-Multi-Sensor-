from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import get_incident, list_incidents, update_incident
from app.models import Incident, IncidentStatus
from app.util import utcnow

router = APIRouter()


@router.get(
    "/incidents",
    response_model=list[Incident],
    dependencies=[Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN))],
)
def get_incidents(
    status: IncidentStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Incident]:
    return list_incidents(status=status.value if status else None, limit=limit, offset=offset)


@router.post("/incidents/{incident_id}/acknowledge", response_model=Incident)
def acknowledge_incident(
    incident_id: int, principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN))
) -> Incident:
    incident = get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status != IncidentStatus.OPEN:
        raise HTTPException(
            status_code=409, detail=f"Incident is '{incident.status.value}', not 'open'"
        )
    incident.status = IncidentStatus.ACKNOWLEDGED
    incident.acknowledged_by = principal.name
    return update_incident(incident)


@router.post("/incidents/{incident_id}/resolve", response_model=Incident)
def resolve_incident(
    incident_id: int, principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN))
) -> Incident:
    incident = get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status == IncidentStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="Incident is already 'resolved'")
    incident.status = IncidentStatus.RESOLVED
    incident.closed_at = utcnow()
    if incident.acknowledged_by is None:
        incident.acknowledged_by = principal.name
    return update_incident(incident)
