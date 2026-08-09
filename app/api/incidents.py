from fastapi import APIRouter, HTTPException, Query

from app.db import get_incident, list_incidents, update_incident
from app.models import Incident, IncidentStatus

router = APIRouter()


@router.get("/incidents", response_model=list[Incident])
def get_incidents(status: IncidentStatus | None = Query(default=None)) -> list[Incident]:
    return list_incidents(status=status.value if status else None)


@router.post("/incidents/{incident_id}/acknowledge", response_model=Incident)
def acknowledge_incident(incident_id: int) -> Incident:
    incident = get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status != IncidentStatus.OPEN:
        raise HTTPException(
            status_code=409, detail=f"Incident is '{incident.status.value}', not 'open'"
        )
    incident.status = IncidentStatus.ACKNOWLEDGED
    return update_incident(incident)
