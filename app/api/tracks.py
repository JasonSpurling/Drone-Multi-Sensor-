from fastapi import APIRouter, HTTPException, Query

from app.db import get_track, list_tracks
from app.models import Track, TrackStatus
from app.tracking import expire_stale_tracks

router = APIRouter()


@router.get("/tracks", response_model=list[Track])
def get_tracks(status: TrackStatus | None = Query(default=None)) -> list[Track]:
    expire_stale_tracks()
    return list_tracks(status=status.value if status else None)


@router.get("/tracks/{track_id}", response_model=Track)
def get_track_by_id(track_id: int) -> Track:
    expire_stale_tracks()
    track = get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return track
