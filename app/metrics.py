"""Prometheus metrics. Counters are incremented at their natural choke
points (associate_detection, _open_incident); gauges are computed live at
scrape time in the /api/metrics handler rather than tracked incrementally,
so they can't drift from the database.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

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

# Generic HTTP-layer visibility -- distinct from the domain counters above,
# which only cover the detection-ingest path. Labeled by the route's *path
# template* ("/api/tracks/{track_id}"), never the raw request path: the raw
# path is client-controlled (a 404 probe, a malformed URL) and would give
# an unbounded, attacker-influenced label cardinality, which is exactly
# what Prometheus's data model warns against and what would make this
# endpoint itself a resource-exhaustion vector. An unmatched route (a real
# 404) is labeled "unmatched" instead of its raw path for the same reason.
http_requests_total = Counter(
    "drone_http_requests_total", "Total HTTP requests handled", ["method", "path", "status"]
)
http_request_duration_seconds = Histogram(
    "drone_http_request_duration_seconds", "HTTP request latency in seconds", ["method", "path"]
)
http_exceptions_total = Counter(
    "drone_http_exceptions_total",
    "Total requests that raised an unhandled exception (before FastAPI's own error response)",
    ["method", "path"],
)
