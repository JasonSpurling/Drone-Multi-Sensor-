import json
import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import ROLE_ADMIN, ROLE_INGEST, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.config import MAX_BATCH_SIZE, MAX_DETECTION_CLOCK_SKEW_SECONDS
from app.db import list_detections, record_audit, set_detection_human_label
from app.metrics import (
    clock_skew_rejected_total,
    detections_ingested_total,
    rate_limited_total,
    site_rate_limited_total,
)
from app.models import Detection, DetectionLabelInput
from app.ratelimit import detection_rate_limiter, site_detection_rate_limiter
from app.tracking import associate_detection, associate_detections_batch
from app.util import utcnow

router = APIRouter()

_viewer_roles = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)


def _check_rate_limit(sensor_id: str, site_id: int) -> None:
    # Per-sensor first: it's the more specific, more common case (one
    # malfunctioning sensor), and cheaper to explain in the 429 detail. The
    # site-wide check is the backstop against many sensors collectively
    # over budget -- see app/config.py's GLOBAL_RATE_LIMIT_PER_SECOND.
    if not detection_rate_limiter.allow(sensor_id):
        rate_limited_total.labels(sensor_id=sensor_id).inc()
        retry_after_s = math.ceil(detection_rate_limiter.retry_after(sensor_id))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for sensor '{sensor_id}'",
            headers={"Retry-After": str(retry_after_s)},
        )
    site_key = str(site_id)
    if not site_detection_rate_limiter.allow(site_key):
        site_rate_limited_total.labels(site_id=site_key).inc()
        retry_after_s = math.ceil(site_detection_rate_limiter.retry_after(site_key))
        raise HTTPException(
            status_code=429,
            detail="Site-wide detection rate limit exceeded (many sensors collectively over budget)",
            headers={"Retry-After": str(retry_after_s)},
        )


def _check_clock_skew(detection: Detection) -> None:
    """Rejects a detection whose (client-supplied) timestamp is
    implausibly far from the server's own clock -- catches an unsynced
    sensor before its skewed timestamp reaches app.tracking's Kalman
    predict step, where an inflated dt would balloon the predicted
    position's uncertainty on every detection from that sensor. See
    app/config.py's MAX_DETECTION_CLOCK_SKEW_SECONDS docstring.
    """
    skew_s = abs((utcnow() - detection.timestamp).total_seconds())
    if skew_s > MAX_DETECTION_CLOCK_SKEW_SECONDS:
        clock_skew_rejected_total.labels(sensor_id=detection.sensor_id).inc()
        raise HTTPException(
            status_code=400,
            detail=(
                f"Detection timestamp for sensor '{detection.sensor_id}' is {skew_s:.0f}s from the "
                f"server's clock (limit {MAX_DETECTION_CLOCK_SKEW_SECONDS:.0f}s) -- check that sensor's "
                "clock is synchronized (e.g. NTP) and reporting UTC."
            ),
        )


@router.get("/detections", response_model=list[Detection])
def get_detections(
    sensor_id: str | None = Query(default=None),
    track_id: int | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_role(*_viewer_roles)),
) -> list[Detection]:
    """Raw ingested detections, independent of any track -- unlike
    GET /api/tracks/{id}/history (app.api.tracks), which is scoped to one
    already-known track's path, this is for sensor-level QA/debugging
    ("what has sensor X actually reported in the last hour") without
    needing to first find which track(s) that spans. Every detection
    this app ever ingests gets associated with some track (app.tracking
    always spawns one if nothing matches), so this and /tracks/{id}/history
    overlap in content -- they differ in what you're allowed to already
    know before you ask. track_id is here too (app.db.list_detections
    already supported it) so a caller who *does* already know a track
    can still combine it with sensor_id/start/end, rather than needing
    to switch endpoints just to add a time window to a track's history.
    """
    return list_detections(
        site_id=principal.site_id, track_id=track_id, sensor_id=sensor_id,
        start=start, end=end, limit=limit, offset=offset,
    )


@router.post(
    "/detections",
    response_model=Detection,
    status_code=201,
)
def ingest_detection(
    detection: Detection, principal: Principal = Depends(require_role(ROLE_INGEST, ROLE_ADMIN))
) -> Detection:
    _check_rate_limit(detection.sensor_id, principal.site_id)
    _check_clock_skew(detection)
    detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
    detection.id = None
    detection.track_id = None
    # Never client-supplied: georeferenced and site_id for the reasons
    # given elsewhere (a signed detection's signature must verify against
    # a position it actually signed; a client claiming a site it doesn't
    # hold a key for would let it write into another site's data).
    # human_label is ground truth an operator assigns after the fact via
    # PUT /api/detections/{id}/label (gated to ROLE_OPERATOR/ROLE_ADMIN,
    # not this ROLE_INGEST endpoint) -- a sensor shouldn't get to supply
    # its own training label at ingest time.
    detection.georeferenced = False
    detection.site_id = principal.site_id
    detection.human_label = None
    return associate_detection(detection)


@router.post(
    "/detections/batch",
    response_model=list[Detection],
    status_code=201,
)
def ingest_detections_batch(
    detections: list[Detection], principal: Principal = Depends(require_role(ROLE_INGEST, ROLE_ADMIN))
) -> list[Detection]:
    """Ingest a batch of simultaneous detections -- e.g. every plot from
    one radar scan/sweep -- resolved jointly via global nearest neighbor
    instead of one at a time. Use this instead of repeated POST
    /detections calls whenever a sensor naturally reports several
    detections at once and two tracks might be close together: joint
    resolution can't swap which detection goes to which track the way
    resolving them one at a time, greedily, can. See
    app.tracking.associate_detections_batch for the scope note on not
    mixing multiple sensors' simultaneous reports into one batch.
    """
    if len(detections) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Batch of {len(detections)} detections exceeds the {MAX_BATCH_SIZE} limit "
            "(DRONE_MAX_BATCH_SIZE) -- split into smaller batches.",
        )
    for detection in detections:
        _check_rate_limit(detection.sensor_id, principal.site_id)
        _check_clock_skew(detection)
    for detection in detections:
        detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
        detection.id = None
        detection.track_id = None
        detection.georeferenced = False
        detection.site_id = principal.site_id
        detection.human_label = None
    return associate_detections_batch(detections)


@router.put("/detections/{detection_id}/label", response_model=Detection)
def label_detection(
    detection_id: int,
    body: DetectionLabelInput,
    principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN)),
) -> Detection:
    """An operator's ground-truth label for one detection -- see
    app.models.Detection.human_label. Building these up over real
    sensor traffic is what GET /api/ml/training-data/export turns into a
    CSV app.ml.train can actually learn from (see app/ml/__init__.py for
    why this repo ships no such labeled data itself).
    """
    label_value = body.label.value if body.label is not None else None
    updated = set_detection_human_label(detection_id, principal.site_id, label_value)
    if updated is None:
        raise HTTPException(status_code=404, detail="Detection not found")
    record_audit(
        site_id=principal.site_id,
        actor=principal.name,
        action="detection.label",
        target=str(detection_id),
        detail=json.dumps({"label": label_value}),
    )
    return updated
