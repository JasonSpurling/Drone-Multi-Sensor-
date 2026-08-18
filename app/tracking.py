"""Track association and lifecycle management.

Each track carries a constant-velocity Kalman filter (app.kalman) running in
a local tangent-plane frame anchored at the track's first fix (app.geo).
Incoming detections are gated against every active track's *predicted*
position (not just its last raw fix) using squared Mahalanobis distance, so
gating naturally tightens for a stable track and widens for a maneuvering
or infrequently-updated one, and a fast-moving object doesn't fall outside
a raw-distance gate sized for a hovering one.
"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from datetime import datetime, timedelta

from app.config import (
    FUSION_HISTORY_LIMIT,
    KALMAN_INITIAL_VELOCITY_SIGMA_MPS,
    KALMAN_MEASUREMENT_SIGMA_M,
    KALMAN_PROCESS_NOISE,
    TRACK_DISTANCE_GATE_M,
    TRACK_DROP_SECONDS,
    TRACK_GATE_CHI2,
    TRACK_STALE_SECONDS,
    TRACK_TIME_GATE_SECONDS,
)
from app.db import (
    KalmanStateRecord,
    create_detection,
    create_track,
    get_kalman_state,
    list_recent_detections,
    list_tracks,
    update_track,
    upsert_kalman_state,
)
from app.fusion import fuse_classification
from app.geo import haversine_distance_m, latlon_to_local_m, local_m_to_latlon
from app.georeference import georeference
from app.incidents import check_predicted_incursions, check_zone_incidents
from app.kalman import ConstantVelocityKalmanFilter
from app.models import Classification, Detection, Track, TrackStatus
from app.util import utcnow

logger = logging.getLogger(__name__)

# Guards the read-then-write track association/incident-creation sequence
# below. FastAPI runs sync routes (like POST /api/detections) in a thread
# pool, so concurrent requests can otherwise both miss each other's
# in-flight track/incident and create duplicates.
_association_lock = threading.Lock()

# A detection's assumed 1-sigma position error shrinks with confidence, so
# a low-confidence return is trusted less by the filter; clamped so a
# near-zero confidence doesn't blow the sigma up to infinity.
_MIN_CONFIDENCE_FOR_MEASUREMENT_SIGMA = 0.1


def _measurement_variance(confidence: float) -> float:
    sigma = KALMAN_MEASUREMENT_SIGMA_M / max(confidence, _MIN_CONFIDENCE_FOR_MEASUREMENT_SIGMA)
    return sigma * sigma


def _load_filter(state: KalmanStateRecord) -> ConstantVelocityKalmanFilter:
    return ConstantVelocityKalmanFilter(
        x=state.x_m, y=state.y_m, vx=state.vx_mps, vy=state.vy_mps, covariance=state.covariance
    )


def _save_filter(
    track_id: int, ref_lat: float, ref_lon: float, kf: ConstantVelocityKalmanFilter, at: datetime
) -> None:
    upsert_kalman_state(
        KalmanStateRecord(
            track_id=track_id,
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            x_m=kf.x,
            y_m=kf.y,
            vx_mps=kf.vx,
            vy_mps=kf.vy,
            covariance=kf.covariance,
            updated_at=at,
        )
    )


def _apply_filter_to_track(track: Track, kf: ConstantVelocityKalmanFilter, ref_lat: float, ref_lon: float) -> None:
    track.latitude, track.longitude = local_m_to_latlon(kf.x, kf.y, ref_lat, ref_lon)
    speed = math.hypot(kf.vx, kf.vy)
    track.speed_mps = speed
    # Heading is undefined for a stationary/near-stationary track; leave the
    # previous heading in place rather than snapping to an arbitrary value.
    if speed > 0.05:
        track.heading_deg = math.degrees(math.atan2(kf.vx, kf.vy)) % 360.0
    track.position_uncertainty_m = kf.position_uncertainty_m()


def expire_stale_tracks(now: datetime | None = None) -> None:
    """Sweep tracks: active -> lost -> closed once each has gone quiet too long."""
    now = now or utcnow()

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


class _Match:
    def __init__(self, track: Track, kf: ConstantVelocityKalmanFilter, ref_lat: float, ref_lon: float) -> None:
        self.track = track
        self.kf = kf
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon


def _find_matching_track(detection: Detection, measurement_variance: float) -> _Match | None:
    """Gate the detection against every active track's Kalman-predicted
    position. Returns the best (lowest squared-Mahalanobis-distance) match
    within gate, along with its filter already predicted forward to the
    detection's timestamp, ready for update() -- or None if nothing gates.
    """
    if detection.latitude is None or detection.longitude is None:
        return None

    best: tuple[_Match, float] | None = None
    for track in list_tracks(status=TrackStatus.ACTIVE.value):
        if track.id is None or track.latitude is None or track.longitude is None:
            continue
        if abs(detection.timestamp - track.last_seen) > timedelta(seconds=TRACK_TIME_GATE_SECONDS):
            continue

        # Cheap coarse prefilter before the more expensive Mahalanobis math.
        coarse_distance = haversine_distance_m(
            detection.latitude, detection.longitude, track.latitude, track.longitude
        )
        if coarse_distance > TRACK_DISTANCE_GATE_M:
            continue

        state = get_kalman_state(track.id)
        if state is None:
            continue
        kf = _load_filter(state)
        dt_s = (detection.timestamp - state.updated_at).total_seconds()
        kf.predict(dt_s, KALMAN_PROCESS_NOISE)

        zx, zy = latlon_to_local_m(detection.latitude, detection.longitude, state.ref_lat, state.ref_lon)
        mahalanobis_sq = kf.mahalanobis_sq(zx, zy, measurement_variance)
        if mahalanobis_sq > TRACK_GATE_CHI2:
            continue
        if best is None or mahalanobis_sq < best[1]:
            best = (_Match(track, kf, state.ref_lat, state.ref_lon), mahalanobis_sq)

    return best[0] if best else None


def associate_detection(detection: Detection) -> Detection:
    """Gate an incoming detection against existing tracks, update or spawn a
    track, persist the detection against it, and return the stored detection.
    """
    detection = georeference(detection)
    measurement_variance = _measurement_variance(detection.confidence)

    with _association_lock:
        expire_stale_tracks(detection.timestamp)

        match = None
        if detection.latitude is not None and detection.longitude is not None:
            match = _find_matching_track(detection, measurement_variance)

        if match is None:
            track = create_track(
                Track(
                    track_uid=str(uuid.uuid4()),
                    first_seen=detection.timestamp,
                    last_seen=detection.timestamp,
                    status=TrackStatus.ACTIVE,
                    classification=Classification.UNKNOWN,
                    latitude=detection.latitude,
                    longitude=detection.longitude,
                    altitude_m=detection.altitude_m,
                )
            )
            if detection.latitude is not None and detection.longitude is not None:
                kf = ConstantVelocityKalmanFilter(
                    x=0.0,
                    y=0.0,
                    position_variance=measurement_variance,
                    velocity_variance=KALMAN_INITIAL_VELOCITY_SIGMA_MPS**2,
                )
                _save_filter(track.id, detection.latitude, detection.longitude, kf, detection.timestamp)
                _apply_filter_to_track(track, kf, detection.latitude, detection.longitude)
            logger.info(
                "New track %s from sensor=%s confidence=%.2f",
                track.track_uid, detection.sensor_id, detection.confidence,
            )
        else:
            track, kf, ref_lat, ref_lon = match.track, match.kf, match.ref_lat, match.ref_lon
            zx, zy = latlon_to_local_m(detection.latitude, detection.longitude, ref_lat, ref_lon)
            kf.update(zx, zy, measurement_variance)
            _save_filter(track.id, ref_lat, ref_lon, kf, detection.timestamp)
            _apply_filter_to_track(track, kf, ref_lat, ref_lon)

            track.last_seen = detection.timestamp
            track.altitude_m = detection.altitude_m
            logger.debug("Detection from sensor=%s associated with track %s", detection.sensor_id, track.track_uid)

        # Persist the detection before fusing classification, so this
        # detection's own vote is included in the track's history.
        detection.track_id = track.id
        persisted = create_detection(detection)

        # Multi-sensor classification fusion (app/fusion.py): the fused
        # label from every recent detection decides, not just this one.
        # A detection can always upgrade a track to DRONE (except away
        # from the trusted ADS-B AIRCRAFT label), since misidentifying a
        # real drone as a bird/unknown/friendly and never re-flagging it
        # is the unsafe failure mode. Any other non-UNKNOWN label only
        # fills in a still-unknown classification, never overwrites one.
        history = list_recent_detections(track.id, FUSION_HISTORY_LIMIT)
        fused_label = fuse_classification(history)
        if fused_label == Classification.DRONE and track.classification != Classification.AIRCRAFT:
            track.classification = fused_label
        elif track.classification == Classification.UNKNOWN and fused_label != Classification.UNKNOWN:
            track.classification = fused_label
        update_track(track)

        check_zone_incidents(track)
        check_predicted_incursions(track)

        return persisted
