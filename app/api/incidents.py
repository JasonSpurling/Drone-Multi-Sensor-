from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import get_incident, get_track, get_zone, list_detections, list_incidents, record_audit, update_incident
from app.models import Incident, IncidentStatus
from app.reporting import build_after_action_report
from app.util import utcnow

router = APIRouter()


@router.get("/incidents", response_model=list[Incident])
def get_incidents(
    status: IncidentStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)),
) -> list[Incident]:
    return list_incidents(
        site_id=principal.site_id, status=status.value if status else None, limit=limit, offset=offset
    )


@router.get("/incidents/{incident_id}/report")
def get_incident_after_action_report(
    incident_id: int, principal: Principal = Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN))
) -> dict:
    """Full single-incident after-action summary -- what was seen, when,
    by which sensors, how it was classified, and how it was responded to.
    See app.reporting.build_after_action_report for the shape; the
    dashboard renders this as a printable report (window.print()).
    """
    incident = get_incident(incident_id, principal.site_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    track = get_track(incident.track_id, principal.site_id) if incident.track_id else None
    zone = get_zone(incident.zone_id, principal.site_id) if incident.zone_id else None
    detections = list_detections(principal.site_id, track_id=incident.track_id) if incident.track_id else []
    return build_after_action_report(incident, track, zone, detections)


@router.post("/incidents/{incident_id}/acknowledge", response_model=Incident)
def acknowledge_incident(
    incident_id: int, principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN))
) -> Incident:
    incident = get_incident(incident_id, principal.site_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status != IncidentStatus.OPEN:
        raise HTTPException(
            status_code=409, detail=f"Incident is '{incident.status.value}', not 'open'"
        )
    incident.status = IncidentStatus.ACKNOWLEDGED
    incident.acknowledged_by = principal.name
    updated = update_incident(incident)
    record_audit(
        site_id=principal.site_id,
        actor=principal.name,
        action="incident.acknowledge",
        target=str(incident_id),
    )
    return updated


@router.post("/incidents/{incident_id}/investigate", response_model=Incident)
def investigate_incident(
    incident_id: int, principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN))
) -> Incident:
    """An operator's deliberate "I'm actively working this one" step,
    distinct from just having acknowledged it -- see IncidentStatus.
    INVESTIGATING's docstring. Only reachable from 'acknowledged', the
    same "you looked at it first" ordering acknowledge -> resolve already
    enforces.
    """
    incident = get_incident(incident_id, principal.site_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status != IncidentStatus.ACKNOWLEDGED:
        raise HTTPException(
            status_code=409, detail=f"Incident is '{incident.status.value}', not 'acknowledged'"
        )
    incident.status = IncidentStatus.INVESTIGATING
    updated = update_incident(incident)
    record_audit(
        site_id=principal.site_id, actor=principal.name, action="incident.investigate", target=str(incident_id)
    )
    return updated


@router.post("/incidents/{incident_id}/resolve", response_model=Incident)
def resolve_incident(
    incident_id: int, principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN))
) -> Incident:
    incident = get_incident(incident_id, principal.site_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status == IncidentStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="Incident is already 'resolved'")
    incident.status = IncidentStatus.RESOLVED
    incident.closed_at = utcnow()
    if incident.acknowledged_by is None:
        incident.acknowledged_by = principal.name
    updated = update_incident(incident)
    record_audit(
        site_id=principal.site_id, actor=principal.name, action="incident.resolve", target=str(incident_id)
    )
    return updated
