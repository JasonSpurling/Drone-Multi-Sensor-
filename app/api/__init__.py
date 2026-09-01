"""FastAPI route handlers, one module per resource -- each defines an
APIRouter that app/main.py imports and mounts under /api. Split this way
(rather than one flat routes.py) so a resource's endpoints, its
request/response shaping, and the RBAC role it requires all live next to
each other: detections.py, tracks.py, zones.py, sensors.py,
sensor_registry.py, incidents.py, sites.py, keys.py,
authorized_operators.py, auth_sso.py, ml_training.py, reports.py,
audit_log.py, live.py (the WebSocket push endpoint), health.py, metrics.py.

The actual domain logic (tracking, fusion, georeferencing, ...) lives
above this package in app/ itself -- these modules stay thin: parse the
request, call into app/, shape the response, apply auth via
app.auth.require_role().
"""
