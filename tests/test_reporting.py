from datetime import datetime, timedelta

from app.models import Incident, IncidentSeverity, IncidentStatus, IncidentType
from app.reporting import build_incident_report

START = datetime(2026, 1, 1)
END = datetime(2026, 1, 8)


def make_incident(
    incident_type=IncidentType.ZONE_INCURSION, severity=IncidentSeverity.MEDIUM,
    status=IncidentStatus.OPEN, opened_at=None, closed_at=None, acknowledged_by=None,
) -> Incident:
    return Incident(
        incident_uid="uid", incident_type=incident_type, severity=severity, status=status,
        track_id=1, zone_id=1, opened_at=opened_at or START, closed_at=closed_at,
        acknowledged_by=acknowledged_by,
    )


def test_empty_report_has_zero_counts():
    report = build_incident_report([], START, END)
    assert report["total_incidents"] == 0
    assert report["by_type"] == {}
    assert report["resolution_time_seconds"]["mean"] is None


def test_total_incidents_counted():
    incidents = [make_incident(), make_incident(), make_incident()]
    report = build_incident_report(incidents, START, END)
    assert report["total_incidents"] == 3


def test_breakdown_by_type():
    incidents = [
        make_incident(incident_type=IncidentType.ZONE_INCURSION),
        make_incident(incident_type=IncidentType.ZONE_INCURSION),
        make_incident(incident_type=IncidentType.LOITERING),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["by_type"] == {"zone_incursion": 2, "loitering": 1}


def test_breakdown_by_severity_and_status():
    incidents = [
        make_incident(severity=IncidentSeverity.HIGH, status=IncidentStatus.OPEN),
        make_incident(severity=IncidentSeverity.LOW, status=IncidentStatus.RESOLVED),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["by_severity"] == {"high": 1, "low": 1}
    assert report["by_status"] == {"open": 1, "resolved": 1}


def test_resolution_time_only_counts_closed_incidents():
    incidents = [
        make_incident(opened_at=START, closed_at=START + timedelta(hours=1)),
        make_incident(opened_at=START, closed_at=None),  # still open, not counted
    ]
    report = build_incident_report(incidents, START, END)
    assert report["resolution_time_seconds"]["count"] == 1
    assert report["resolution_time_seconds"]["mean"] == 3600.0
    assert report["resolution_time_seconds"]["median"] == 3600.0
    assert report["resolution_time_seconds"]["max"] == 3600.0


def test_unacknowledged_count():
    incidents = [
        make_incident(acknowledged_by=None),
        make_incident(acknowledged_by="operator-1"),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["unacknowledged_count"] == 1


def test_daily_counts_grouped_by_date():
    incidents = [
        make_incident(opened_at=datetime(2026, 1, 1, 9)),
        make_incident(opened_at=datetime(2026, 1, 1, 15)),
        make_incident(opened_at=datetime(2026, 1, 2, 9)),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["daily_counts"] == {"2026-01-01": 2, "2026-01-02": 1}


def test_range_is_echoed_in_report():
    report = build_incident_report([], START, END)
    assert report["range"]["start"] == START.isoformat()
    assert report["range"]["end"] == END.isoformat()
