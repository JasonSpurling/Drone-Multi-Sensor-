from fastapi import APIRouter

from app.models import Detection
from app.tracking import associate_detection

router = APIRouter()


@router.post("/detections", response_model=Detection, status_code=201)
def ingest_detection(detection: Detection) -> Detection:
    detection.id = None
    detection.track_id = None
    return associate_detection(detection)
