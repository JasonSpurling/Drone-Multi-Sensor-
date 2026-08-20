"""Prometheus metrics. Counters are incremented at their natural choke
points (associate_detection, _open_incident); gauges are computed live at
scrape time in the /api/metrics handler rather than tracked incrementally,
so they can't drift from the database.
"""

from __future__ import annotations

from prometheus_client import Counter

detections_ingested_total = Counter(
    "drone_detections_ingested_total", "Total detections ingested", ["sensor_type"]
)
incidents_opened_total = Counter(
    "drone_incidents_opened_total", "Total incidents opened", ["incident_type", "severity"]
)
rate_limited_total = Counter(
    "drone_rate_limited_total", "Total detections rejected by the rate limiter", ["sensor_id"]
)
clock_skew_rejected_total = Counter(
    "drone_clock_skew_rejected_total",
    "Total detections rejected for a timestamp too far from the server's own clock",
    ["sensor_id"],
)
