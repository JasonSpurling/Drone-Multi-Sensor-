"""Track association and lifecycle management (gating, update, expiry)."""

from __future__ import annotations

import logging
import math
import threading
import uuid
from datetime import datetime, timedelta

from app.classification import classify
from app.config import (
    TRACK_DISTANCE_GATE_M,
    TRACK_DROP_SECONDS,
    TRACK_STALE_SECONDS,
    TRACK_TIME_GATE_SECONDS,
)
from app.db import create_detection, create_track, list_tracks, update_track
from app.incidents import check_zone_incidents
from app.models import Classification, Detection, Track, TrackStatus

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000.0

# Guards the read-then-write track association/incident-creation sequence
# below. FastAPI runs sync routes (like POST /api/detections) in a thread
# pool, so concurrent requests can otherwise both miss each other's
# in-flight track/incident and create duplicates.
_association_lock = threading.Lock()


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
            logger.info("Track %s -> lost (last seen %s)", track.track_uid, track.last_seen)

    for track in list_tracks(status=TrackStatus.LOST.value):
        if now - track.last_seen > timedelta(seconds=TRACK_DROP_SECONDS):
            track.status = TrackStatus.CLOSED
            update_track(track)
            logger.info("Track %s -> closed (last seen %s)", track.track_uid, track.last_seen)


def _find_matching_track(detection: Detection) -> Track | None:
    if detection.latitude is None or detection.longitude is None:
        return None

    best_track: Track | None = None
    best_distance = TRACK_DISTANCE_GATE_M
    for track in list_tracks(status=TrackStatus.ACTIVE.value):
        if track.latitude is None or track.longitude is None:
            continue
        if abs(detection.timestamp - track.last_seen) > timedelta(seconds=TRACK_TIME_GATE_SECONDS):
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
    label = classify(detection.sensor_type, detection.confidence)

    with _association_lock:
        expire_stale_tracks(detection.timestamp)

        track = _find_matching_track(detection)
        if track is None:
            track = create_track(
                Track(
                    track_uid=str(uuid.uuid4()),
                    first_seen=detection.timestamp,
                    last_seen=detection.timestamp,
                    status=TrackStatus.ACTIVE,
                    classification=label,
                    latitude=detection.latitude,
                    longitude=detection.longitude,
                    altitude_m=detection.altitude_m,
                )
            )
            logger.info(
                "New track %s (%s) from sensor=%s confidence=%.2f",
                track.track_uid, label.value, detection.sensor_id, detection.confidence,
            )
        else:
            track.last_seen = detection.timestamp
            track.latitude = detection.latitude
            track.longitude = detection.longitude
            track.altitude_m = detection.altitude_m
            # A detection can always upgrade a track to DRONE (except away
            # from the trusted ADS-B AIRCRAFT label) even if it was already
            # classified as something else, since misidentifying a real
            # drone as a bird/unknown and never re-flagging it is the unsafe
            # failure mode. Any other non-UNKNOWN label only fills in a
            # still-unknown classification, never overwrites one.
            if label == Classification.DRONE and track.classification != Classification.AIRCRAFT:
                track.classification = label
            elif track.classification == Classification.UNKNOWN and label != Classification.UNKNOWN:
                track.classification = label
            update_track(track)
            logger.debug("Detection from sensor=%s associated with track %s", detection.sensor_id, track.track_uid)

        check_zone_incidents(track)

        detection.track_id = track.id
        return create_detection(detection)
