"""Track association and lifecycle management (gating, update, expiry)."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from app.db import create_detection, create_track, list_tracks, update_track
from app.models import Detection, Track, TrackStatus

# Association gates: a detection may only join a track if it arrives within
# TIME_GATE_SECONDS of the track's last update and within DISTANCE_GATE_M of
# its last known position.
TIME_GATE_SECONDS = 30
DISTANCE_GATE_M = 500.0

# Lifecycle: an active track with no new detections for TRACK_STALE_SECONDS
# becomes "lost"; a lost track with no recovery for TRACK_DROP_SECONDS becomes
# "closed" and stops accepting associations.
TRACK_STALE_SECONDS = 30
TRACK_DROP_SECONDS = 300

_EARTH_RADIUS_M = 6_371_000.0


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def expire_stale_tracks(now: datetime | None = None) -> None:
    """Sweep tracks: active -> lost -> closed once each has gone quiet too long."""
    now = now or datetime.utcnow()

    for track in list_tracks(status=TrackStatus.ACTIVE.value):
        if now - track.last_seen > timedelta(seconds=TRACK_STALE_SECONDS):
            track.status = TrackStatus.LOST
            update_track(track)

    for track in list_tracks(status=TrackStatus.LOST.value):
        if now - track.last_seen > timedelta(seconds=TRACK_DROP_SECONDS):
            track.status = TrackStatus.CLOSED
            update_track(track)


def _find_matching_track(detection: Detection) -> Track | None:
    if detection.latitude is None or detection.longitude is None:
        return None

    best_track: Track | None = None
    best_distance = DISTANCE_GATE_M
    for track in list_tracks(status=TrackStatus.ACTIVE.value):
        if track.latitude is None or track.longitude is None:
            continue
        if abs(detection.timestamp - track.last_seen) > timedelta(seconds=TIME_GATE_SECONDS):
            continue
        distance = haversine_distance_m(
            detection.latitude, detection.longitude, track.latitude, track.longitude
        )
        if distance <= best_distance:
            best_distance = distance
            best_track = track
    return best_track


def associate_detection(detection: Detection) -> Detection:
    """Gate an incoming detection against existing tracks, update or spawn a
    track, persist the detection against it, and return the stored detection.
    """
    expire_stale_tracks(detection.timestamp)

    track = _find_matching_track(detection)
    if track is None:
        track = create_track(
            Track(
                track_uid=str(uuid.uuid4()),
                first_seen=detection.timestamp,
                last_seen=detection.timestamp,
                status=TrackStatus.ACTIVE,
                latitude=detection.latitude,
                longitude=detection.longitude,
                altitude_m=detection.altitude_m,
            )
        )
    else:
        track.last_seen = detection.timestamp
        track.latitude = detection.latitude
        track.longitude = detection.longitude
        track.altitude_m = detection.altitude_m
        update_track(track)

    detection.track_id = track.id
    return create_detection(detection)
