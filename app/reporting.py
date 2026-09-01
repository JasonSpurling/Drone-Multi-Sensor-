"""Compliance/historical-analytics rollups over a window of incidents --
what a security compliance officer needs (incursion counts by type/
severity, how long incidents sat open before being acknowledged/resolved)
that GET /api/incidents' plain pagination doesn't answer on its own.

Pure aggregation over already-fetched Incident objects, no DB access here
-- app/api/reports.py does the date-range fetch (app.db.list_incidents_in_range)
and hands the result to build_incident_report, the same pure-logic-module
split used throughout this codebase (app/export.py, app/adapters/mavlink.py).
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime

from app.models import Detection, Incident, Track, Zone


def build_incident_report(incidents: list[Incident], start: datetime, end: datetime) -> dict:
    """Returns a JSON-serializable rollup: total count, breakdowns by
    type/severity/status, resolution-time statistics (mean/median seconds
    from opened_at to closed_at, RESOLVED incidents only -- an open
    incident has no resolution time yet), and a per-day count series for
    trend charting.
    """
    resolution_times: list[float] = [
        (incident.closed_at - incident.opened_at).total_seconds()
        for incident in incidents
        if incident.closed_at is not None
    ]

    daily_counts: Counter[str] = Counter(incident.opened_at.date().isoformat() for incident in incidents)

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "total_incidents": len(incidents),
        "by_type": dict(Counter(incident.incident_type.value for incident in incidents)),
        "by_severity": dict(Counter(incident.severity.value for incident in incidents)),
        "by_status": dict(Counter(incident.status.value for incident in incidents)),
        "resolution_time_seconds": {
            "count": len(resolution_times),
            "mean": statistics.mean(resolution_times) if resolution_times else None,
            "median": statistics.median(resolution_times) if resolution_times else None,
            "max": max(resolution_times) if resolution_times else None,
        },
        "unacknowledged_count": sum(1 for i in incidents if i.acknowledged_by is None),
        "daily_counts": dict(sorted(daily_counts.items())),
    }


def build_after_action_report(
    incident: Incident, track: Track | None, zone: Zone | None, detections: list[Detection]
) -> dict:
    """A single incident's full story for post-incident review: what was
    seen, when, by which sensors, how it was classified, and how it was
    responded to -- everything GET /api/incidents' plain record has, plus
    the track's fused trajectory summary and its raw detection history
    (chronological, from app.db.list_detections -- unlike
    list_recent_detections used for classification fusion, this is the
    *complete* history for the record, not a capped recent window).

    `track`/`zone` are None when the incident's track_id/zone_id no longer
    resolves (e.g. purged by retention) -- the report still renders with
    that section omitted rather than failing outright, since the incident
    row itself (what/when/severity/response) is the part that must never
    be lost to a later purge of the underlying track/detection rows.
    """
    response_seconds = (
        (incident.closed_at - incident.opened_at).total_seconds() if incident.closed_at else None
    )
    sensors_involved = sorted({d.sensor_id for d in detections})

    return {
        "incident": {
            "incident_uid": incident.incident_uid,
            "incident_type": incident.incident_type.value,
            "severity": incident.severity.value,
            "status": incident.status.value,
            "description": incident.description,
            "opened_at": incident.opened_at.isoformat(),
            "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
            "response_time_seconds": response_seconds,
            "acknowledged_by": incident.acknowledged_by,
        },
        "zone": {"name": zone.name, "zone_type": zone.zone_type.value} if zone else None,
        "track": (
            {
                "track_uid": track.track_uid,
                "classification": track.classification.value,
                "first_seen": track.first_seen.isoformat(),
                "last_seen": track.last_seen.isoformat(),
                "final_position": (
                    {"latitude": track.latitude, "longitude": track.longitude, "altitude_m": track.altitude_m}
                ),
                "final_speed_mps": track.speed_mps,
                "final_heading_deg": track.heading_deg,
            }
            if track
            else None
        ),
        "sensors_involved": sensors_involved,
        "detection_count": len(detections),
        "detections": [
            {
                "sensor_id": d.sensor_id,
                "sensor_type": d.sensor_type.value,
                "timestamp": d.timestamp.isoformat(),
                "latitude": d.latitude,
                "longitude": d.longitude,
                "altitude_m": d.altitude_m,
                "confidence": d.confidence,
            }
            for d in detections
        ],
    }
