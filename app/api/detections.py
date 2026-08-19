from fastapi import APIRouter, Depends, HTTPException

from app.auth import ROLE_ADMIN, ROLE_INGEST, require_role
from app.metrics import detections_ingested_total, rate_limited_total
from app.models import Detection
from app.ratelimit import detection_rate_limiter
from app.tracking import associate_detection, associate_detections_batch

router = APIRouter()


def _check_rate_limit(sensor_id: str) -> None:
    if not detection_rate_limiter.allow(sensor_id):
        rate_limited_total.labels(sensor_id=sensor_id).inc()
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded for sensor '{sensor_id}'")


@router.post(
    "/detections",
    response_model=Detection,
    status_code=201,
    dependencies=[Depends(require_role(ROLE_INGEST, ROLE_ADMIN))],
)
def ingest_detection(detection: Detection) -> Detection:
    _check_rate_limit(detection.sensor_id)
    detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
    detection.id = None
    detection.track_id = None
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
    for detection in detections:
        _check_rate_limit(detection.sensor_id)
    for detection in detections:
        detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
        detection.id = None
        detection.track_id = None
    return associate_detections_batch(detections)
