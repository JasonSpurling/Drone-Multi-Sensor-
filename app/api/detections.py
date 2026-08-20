import math

from fastapi import APIRouter, Depends, HTTPException

from app.auth import ROLE_ADMIN, ROLE_INGEST, require_role
from app.config import MAX_BATCH_SIZE, MAX_DETECTION_CLOCK_SKEW_SECONDS
from app.metrics import clock_skew_rejected_total, detections_ingested_total, rate_limited_total
from app.models import Detection
from app.ratelimit import detection_rate_limiter
from app.tracking import associate_detection, associate_detections_batch
from app.util import utcnow

router = APIRouter()


def _check_rate_limit(sensor_id: str) -> None:
    if not detection_rate_limiter.allow(sensor_id):
        rate_limited_total.labels(sensor_id=sensor_id).inc()
        retry_after_s = math.ceil(detection_rate_limiter.retry_after(sensor_id))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for sensor '{sensor_id}'",
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


@router.post(
    "/detections",
    response_model=Detection,
    status_code=201,
    dependencies=[Depends(require_role(ROLE_INGEST, ROLE_ADMIN))],
)
def ingest_detection(detection: Detection) -> Detection:
    _check_rate_limit(detection.sensor_id)
    _check_clock_skew(detection)
    detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
    detection.id = None
    detection.track_id = None
    # Server-computed only (see app/georeference.py) -- a client claiming
    # this would let a signed detection's signature verify against a
    # position it didn't actually sign (app/remote_id.py).
    detection.georeferenced = False
    return associate_detection(detection)


@router.post(
    "/detections/batch",
    response_model=list[Detection],
    status_code=201,
    dependencies=[Depends(require_role(ROLE_INGEST, ROLE_ADMIN))],
)
def ingest_detections_batch(detections: list[Detection]) -> list[Detection]:
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
        _check_rate_limit(detection.sensor_id)
        _check_clock_skew(detection)
    for detection in detections:
        detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
        detection.id = None
        detection.track_id = None
        detection.georeferenced = False
    return associate_detections_batch(detections)
