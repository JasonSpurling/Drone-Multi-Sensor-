from fastapi import APIRouter, HTTPException, Query

from app.db import get_track, list_detections, list_tracks
from app.models import Detection, Track, TrackStatus
from app.tracking import expire_stale_tracks

router = APIRouter()


@router.get("/tracks", response_model=list[Track])
def get_tracks(
    status: TrackStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Track]:
    expire_stale_tracks()
    return list_tracks(status=status.value if status else None, limit=limit, offset=offset)


@router.get("/tracks/{track_id}", response_model=Track)
def get_track_by_id(track_id: int) -> Track:
    expire_stale_tracks()
    track = get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return track


@router.get("/tracks/{track_id}/history", response_model=list[Detection])
def get_track_history(
    track_id: int,
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
) -> list[Detection]:
    """Every detection that fed this track, oldest first -- a replay of
    where it actually was over time. Detection rows aren't deleted when a
    track closes (only DRONE_DETECTION_RETENTION_DAYS purges them by age),
    so this works for a closed/lost track exactly the same as an active
    one -- there's no separate "archive" to look in.
    """
    if get_track(track_id) is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return list_detections(track_id=track_id, limit=limit, offset=offset)
