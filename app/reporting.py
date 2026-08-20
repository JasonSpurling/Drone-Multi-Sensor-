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

from app.models import Incident


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
