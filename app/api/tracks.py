from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.config import INCIDENT_CORROBORATION_MIN_SENSOR_TYPES
from app.db import (
    get_sensor_registration,
    get_track,
    list_detections,
    list_open_incidents_for_track,
    list_tracks,
    record_audit,
    update_track,
)
from app.export import to_csv, to_gpx, to_kml
from app.fusion import contributing_sensor_types, decay_classification_confidence
from app.live import publish as publish_live_event
from app.models import (
    Classification,
    Detection,
    Track,
    TrackClassificationInput,
    TrackIgnoreInput,
    TrackStatus,
    VisualVerificationInput,
    ZoneType,
)
from app.risk import assess_risk, is_approaching_zone
from app.slew_to_cue import compute_camera_cue
from app.tracking import expire_stale_tracks
from app.util import utcnow
from app.zones import nearest_restricted_zone_distance_m, zones_containing_point

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


def _with_computed_fields(track: Track) -> Track:
    """_with_decayed_confidence plus the read-time-only fields
    (corroborating_sensor_types, verified, contributing_sensor_types,
    risk_score, risk_factors, zone_status) GET /api/tracks and GET
    /api/tracks/{id} both need for the dashboard's Verified/Unverified
    grouping, risk-sorted priority queue, and zone-status badge -- a
    couple of extra queries per track (list_recent_detections via
    contributing_sensor_types, list_open_incidents_for_track and
    zones_containing_point for risk/zone status), the same cost
    app.incidents already pays once per opened incident, just now also
    paid per read here.
    """
    track = _with_decayed_confidence(track)
    types = contributing_sensor_types(track)
    track.contributing_sensor_types = sorted(types)
    track.corroborating_sensor_types = len(types)
    track.verified = track.corroborating_sensor_types >= INCIDENT_CORROBORATION_MIN_SENSOR_TYPES
    if track.id is not None and track.site_id is not None:
        open_incidents = list_open_incidents_for_track(track.id, track.site_id)
        zone_distance = None
        if track.latitude is not None and track.longitude is not None:
            zone_distance = nearest_restricted_zone_distance_m(track.latitude, track.longitude, track.site_id)
            inside_restricted = any(
                z.zone_type == ZoneType.RESTRICTED
                for z in zones_containing_point(track.latitude, track.longitude, track.site_id, track.altitude_m)
            )
            track.zone_status = (
                "inside" if inside_restricted else ("approaching" if is_approaching_zone(zone_distance) else "none")
            )
        assessment = assess_risk(track, open_incidents, zone_distance)
        track.risk_score = assessment.score
        track.risk_factors = assessment.factors
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
    return [_with_computed_fields(t) for t in tracks]


@router.get("/tracks/{track_id}", response_model=Track)
def get_track_by_id(track_id: int, principal: Principal = Depends(require_role(*_viewer_roles))) -> Track:
    expire_stale_tracks(principal.site_id)
    track = get_track(track_id, principal.site_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return _with_computed_fields(track)


@router.post("/tracks/{track_id}/classify", response_model=Track)
def classify_track(
    track_id: int,
    body: TrackClassificationInput,
    principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN)),
) -> Track:
    """An operator's deliberate override -- "that's our security team's
    drone" (friendly), "confirmed hostile" (drone), or "Neutral" (clear my
    own earlier call, go back to unclassified) -- distinct from the
    automated fusion ratchet that normally sets Track.classification (see
    that field's own docstring for why it's upgrade-only and never just
    manually reset there). Since a human just looked at this and made the
    FRIENDLY/DRONE call, classification_confidence is set to 1.0 -- the
    same "nothing left to be uncertain about" reasoning an acknowledged
    incident's status carries, not a guess at how confident to report.
    UNKNOWN gets None instead, per that field's own docstring ("no
    meaningful confidence in not knowing") -- "Neutral" is reverting the
    override, not a confident claim of its own.
    """
    track = get_track(track_id, principal.site_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    track.classification = body.classification
    track.classification_confidence = None if body.classification == Classification.UNKNOWN else 1.0
    updated = update_track(track)
    record_audit(
        site_id=principal.site_id,
        actor=principal.name,
        action="track.classify",
        target=str(track_id),
        detail=body.classification.value,
    )
    if updated.site_id is not None:
        publish_live_event(updated.site_id, {"type": "track_update", "track": updated.model_dump(mode="json")})
    return _with_computed_fields(updated)


@router.post("/tracks/{track_id}/ignore", response_model=Track)
def ignore_track(
    track_id: int,
    body: TrackIgnoreInput,
    principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN)),
) -> Track:
    """An operator's deliberate "stop alerting on this" suppression -- see
    Track.ignored's docstring for what it actually changes (new incidents
    only; nothing about the track itself is hidden or altered).
    `duration_minutes` set means "expires on its own after that long"; left
    out means "Indefinitely," same as this endpoint's original behavior.
    Toggled back off with ignored=false, which always clears any expiry too.
    """
    track = get_track(track_id, principal.site_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    track.ignored = body.ignored
    track.ignored_until = (
        utcnow() + timedelta(minutes=body.duration_minutes)
        if body.ignored and body.duration_minutes is not None
        else None
    )
    updated = update_track(track)
    record_audit(
        site_id=principal.site_id,
        actor=principal.name,
        action="track.ignore" if body.ignored else "track.unignore",
        target=str(track_id),
        detail=f"for {body.duration_minutes}m" if body.ignored and body.duration_minutes else "",
    )
    if updated.site_id is not None:
        publish_live_event(updated.site_id, {"type": "track_update", "track": updated.model_dump(mode="json")})
    return _with_computed_fields(updated)


@router.post("/tracks/{track_id}/verify-visual", status_code=204)
def verify_visual(
    track_id: int,
    body: VisualVerificationInput,
    principal: Principal = Depends(require_role(ROLE_OPERATOR, ROLE_ADMIN)),
) -> None:
    """Records a human operator's structured visual-verification judgment
    -- confirmed / a different object / a false detection / unable to
    determine, plus an optional note -- after they actually compared the
    camera feed or a snapshot to what the sensors reported. This never
    touches Track.classification or risk_score itself: it's a record of
    what a person concluded, not a new automated signal, so it can't be
    mistaken for the fusion system's own evidence. Kept in the audit log
    (surfaced on the Timeline tab for admins) rather than a new table,
    the same place every other operator decision on a track already lives.
    """
    if get_track(track_id, principal.site_id) is None:
        raise HTTPException(status_code=404, detail="Track not found")
    detail = body.result if not body.note else f"{body.result}: {body.note}"
    record_audit(
        site_id=principal.site_id,
        actor=principal.name,
        action="track.visual_verify",
        target=str(track_id),
        detail=detail,
    )


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
