from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import list_incidents_in_range
from app.export import incidents_to_csv
from app.reporting import build_incident_report

router = APIRouter()
_viewer_roles = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)


def _parse_range(start: str, end: str) -> tuple[datetime, datetime]:
    try:
        parsed_start = datetime.fromisoformat(start)
        parsed_end = datetime.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from None
    if parsed_end <= parsed_start:
        raise HTTPException(status_code=400, detail="'end' must be after 'start'")
    return parsed_start, parsed_end


@router.get("/reports/incidents")
def get_incident_report(
    start: str = Query(description="ISO-8601 start of the reporting window (inclusive)"),
    end: str = Query(description="ISO-8601 end of the reporting window (exclusive)"),
    principal: Principal = Depends(require_role(*_viewer_roles)),
) -> dict:
    """Aggregate incident rollup for a compliance/analytics review -- see
    app/reporting.py for what's in it. `start`/`end` bound opened_at, the
    same convention app/db.py's purge_old_detections uses for a retention
    cutoff.
    """
    parsed_start, parsed_end = _parse_range(start, end)
    incidents = list_incidents_in_range(parsed_start, parsed_end, principal.site_id)
    return build_incident_report(incidents, parsed_start, parsed_end)


@router.get("/reports/incidents/export")
def export_incident_report(
    start: str = Query(description="ISO-8601 start of the reporting window (inclusive)"),
    end: str = Query(description="ISO-8601 end of the reporting window (exclusive)"),
    principal: Principal = Depends(require_role(*_viewer_roles)),
) -> Response:
    """The raw per-incident records behind get_incident_report's rollup,
    as a downloadable CSV -- for a compliance officer who needs the list
    an aggregate count summarizes, not just the count itself.
    """
    parsed_start, parsed_end = _parse_range(start, end)
    incidents = list_incidents_in_range(parsed_start, parsed_end, principal.site_id)
    body = incidents_to_csv(incidents)
    filename = f"incident-report-{parsed_start.date()}-to-{parsed_end.date()}.csv"
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
