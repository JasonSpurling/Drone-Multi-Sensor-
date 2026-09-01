from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import get_sensor_registration, get_track, list_detections, list_tracks
from app.export import to_csv, to_gpx, to_kml
from app.fusion import decay_classification_confidence
from app.models import Detection, Track, TrackStatus
from app.slew_to_cue import compute_camera_cue
from app.tracking import expire_stale_tracks
from app.util import utcnow

router = APIRouter()

_viewer_roles = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)

_EXPORT_CONTENT_TYPES = {
    "gpx": "application/gpx+xml",
    "kml": "application/vnd.google-earth.kml+xml",
    "csv": "text/csv",
}


def _with_decayed_confidence(track: Track) -> Track:
    """Applies read-time staleness decay to the raw, as-of-last-detection
    classification_confidence app.tracking stored -- see
    app.fusion.decay_classification_confidence's docstring for why this
    happens here (at serve time) instead of being kept current by some
    background job.
    """
    track.classification_confidence = decay_classification_confidence(
        track.classification_confidence, track.last_seen, utcnow()
    )
    return track


@router.get("/tracks", response_model=list[Track])
def get_tracks(
    status: TrackStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_role(*_viewer_roles)),
) -> list[Track]:
    expire_stale_tracks(principal.site_id)
    tracks = list_tracks(
        site_id=principal.site_id, status=status.value if status else None, limit=limit, offset=offset
    )
    return [_with_decayed_confidence(t) for t in tracks]


@router.get("/tracks/{track_id}", response_model=Track)
def get_track_by_id(track_id: int, principal: Principal = Depends(require_role(*_viewer_roles))) -> Track:
    expire_stale_tracks(principal.site_id)
    track = get_track(track_id, principal.site_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return _with_decayed_confidence(track)


@router.get("/tracks/{track_id}/history", response_model=list[Detection])
def get_track_history(
    track_id: int,
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_role(*_viewer_roles)),
) -> list[Detection]:
    """Every detection that fed this track, oldest first -- a replay of
    where it actually was over time. Detection rows aren't deleted when a
    track closes (only DRONE_DETECTION_RETENTION_DAYS purges them by age),
    so this works for a closed/lost track exactly the same as an active
    one -- there's no separate "archive" to look in.
    """
    if get_track(track_id, principal.site_id) is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return list_detections(site_id=principal.site_id, track_id=track_id, limit=limit, offset=offset)


@router.get("/tracks/{track_id}/history/export")
def export_track_history(
    track_id: int,
    format: str = Query(pattern="^(gpx|kml|csv)$"),
    principal: Principal = Depends(require_role(*_viewer_roles)),
) -> Response:
    """The same history as GET .../history, rendered as a downloadable
    file for post-incident review in an external tool: GPX or KML for a
    GIS/mapping application (Google Earth, QGIS, ...), CSV for a
    spreadsheet. See app/export.py for the format details.
    """
    track = get_track(track_id, principal.site_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    detections = list_detections(site_id=principal.site_id, track_id=track_id, limit=10000)
    if format == "gpx":
        body = to_gpx(track, detections)
    elif format == "kml":
        body = to_kml(track, detections)
    else:
        body = to_csv(detections)

    filename = f"track-{track.track_uid}.{format}"
    return Response(
        content=body,
        media_type=_EXPORT_CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tracks/{track_id}/cue/{camera_sensor_id}")
def get_camera_cue(
    track_id: int, camera_sensor_id: str, principal: Principal = Depends(require_role(*_viewer_roles))
) -> dict:
    """Slew-to-cue: the pan/tilt angles a PTZ camera registered as
    `camera_sensor_id` needs to point at this track's current position --
    lets a camera mounted somewhere else entirely be pointed at whatever a
    different sensor (an RF direction-finder, an acoustic array, radar,
    ...) is tracking, instead of a human operator manually panning to
    follow a cue. See app/slew_to_cue.py for the geometry and
    app/adapters/onvif_ptz_bridge.py for actually sending this to a real
    PTZ camera.

    Computed on demand from the track's live position, not cached or
    pushed -- poll this (an external PTZ bridge script, or an operator's
    own tooling) as often as your camera's slew rate can usefully act on.
    """
    track = get_track(track_id, principal.site_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    if track.latitude is None or track.longitude is None:
        raise HTTPException(status_code=409, detail="Track has no resolved position to cue toward")

    camera = get_sensor_registration(camera_sensor_id, principal.site_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera sensor not registered")

    cue = compute_camera_cue(
        camera["latitude"], camera["longitude"], camera["altitude_m"] or 0.0,
        camera["azimuth_reference_deg"],
        track.latitude, track.longitude, track.altitude_m,
    )
    return {
        "track_id": track_id,
        "camera_sensor_id": camera_sensor_id,
        "pan_deg": cue.pan_deg,
        "pan_relative_deg": cue.pan_relative_deg,
        "tilt_deg": cue.tilt_deg,
        "distance_m": cue.distance_m,
    }
