from fastapi import APIRouter, Depends, HTTPException

from app.auth import ROLE_ADMIN, ROLE_INGEST, require_role
from app.metrics import detections_ingested_total, rate_limited_total
from app.models import Detection
from app.ratelimit import detection_rate_limiter
from app.tracking import associate_detection

router = APIRouter()


@router.post(
    "/detections",
    response_model=Detection,
    status_code=201,
    dependencies=[Depends(require_role(ROLE_INGEST, ROLE_ADMIN))],
)
def ingest_detection(detection: Detection) -> Detection:
    if not detection_rate_limiter.allow(detection.sensor_id):
        rate_limited_total.labels(sensor_id=detection.sensor_id).inc()
        raise HTTPException(
            status_code=429, detail=f"Rate limit exceeded for sensor '{detection.sensor_id}'"
        )
    detections_ingested_total.labels(sensor_type=detection.sensor_type.value).inc()
    detection.id = None
    detection.track_id = None
    return associate_detection(detection)
