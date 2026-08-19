"""Track association and lifecycle management.

Each track carries an IMM (Interacting Multiple Model) filter (app.imm)
running in a local tangent-plane frame anchored at the track's first fix
(app.geo) -- a CRUISE/MANEUVER pair of constant-velocity filters that
blend based on how well each explains recent detections, so a sharp turn
or sudden acceleration gets tracked instead of smoothed away as noise.
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

from app.assignment import hungarian_min_cost
from app.config import (
    FUSION_HISTORY_LIMIT,
    KALMAN_CRUISE_PROCESS_NOISE,
    KALMAN_INITIAL_VELOCITY_SIGMA_MPS,
    KALMAN_MANEUVER_PROCESS_NOISE,
    KALMAN_MEASUREMENT_SIGMA_M,
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
from app.imm import IMMFilter
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

# Sentinel cost for a gated-out (detection, track) pair in the batch
# assignment matrix -- large enough that Hungarian will only ever choose
# it over a real gated match (bounded by TRACK_GATE_CHI2) or the
# decline-to-match dummy column (also TRACK_GATE_CHI2), never in between.
_ASSIGNMENT_BLOCKED_COST = 1e12


def _measurement_variance(confidence: float) -> float:
    sigma = KALMAN_MEASUREMENT_SIGMA_M / max(confidence, _MIN_CONFIDENCE_FOR_MEASUREMENT_SIGMA)
    return sigma * sigma


def _model_to_dict(model: ConstantVelocityKalmanFilter) -> dict:
    return {"x": model.x, "y": model.y, "vx": model.vx, "vy": model.vy, "covariance": model.covariance}


def _model_from_dict(data: dict) -> ConstantVelocityKalmanFilter:
    return ConstantVelocityKalmanFilter(
        x=data["x"], y=data["y"], vx=data["vx"], vy=data["vy"], covariance=data["covariance"]
    )


def _load_filter(state: KalmanStateRecord) -> IMMFilter:
    return IMMFilter(
        x=0.0,
        y=0.0,
        cruise_process_noise=KALMAN_CRUISE_PROCESS_NOISE,
        maneuver_process_noise=KALMAN_MANEUVER_PROCESS_NOISE,
        mode_probabilities=state.mode_probabilities,
        models=[_model_from_dict(m) for m in state.models],
    )


def _save_filter(track_id: int, ref_lat: float, ref_lon: float, imm: IMMFilter, at: datetime) -> None:
    upsert_kalman_state(
        KalmanStateRecord(
            track_id=track_id,
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            models=[_model_to_dict(model) for model in imm.models],
            mode_probabilities=imm.mode_probabilities,
            updated_at=at,
        )
    )


def _apply_filter_to_track(track: Track, imm: IMMFilter, ref_lat: float, ref_lon: float) -> None:
    track.latitude, track.longitude = local_m_to_latlon(imm.x, imm.y, ref_lat, ref_lon)
    speed = math.hypot(imm.vx, imm.vy)
    track.speed_mps = speed
    # Heading is undefined for a stationary/near-stationary track; leave the
    # previous heading in place rather than snapping to an arbitrary value.
    if speed > 0.05:
        track.heading_deg = math.degrees(math.atan2(imm.vx, imm.vy)) % 360.0
    track.position_uncertainty_m = imm.position_uncertainty_m()
    track.maneuver_probability = imm.maneuver_probability


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
    def __init__(self, track: Track, imm: IMMFilter, ref_lat: float, ref_lon: float) -> None:
        self.track = track
        self.imm = imm
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon


def _find_matching_track(detection: Detection, measurement_variance: float) -> _Match | None:
    """Gate the detection against every active track's IMM-predicted
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
        imm = _load_filter(state)
        dt_s = (detection.timestamp - state.updated_at).total_seconds()
        imm.predict(dt_s)

        zx, zy = latlon_to_local_m(detection.latitude, detection.longitude, state.ref_lat, state.ref_lon)
        mahalanobis_sq = imm.mahalanobis_sq(zx, zy, measurement_variance)
        if mahalanobis_sq > TRACK_GATE_CHI2:
            continue
        if best is None or mahalanobis_sq < best[1]:
            best = (_Match(track, imm, state.ref_lat, state.ref_lon), mahalanobis_sq)

    return best[0] if best else None


def _spawn_track(detection: Detection, measurement_variance: float) -> Track:
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
        imm = IMMFilter(
            x=0.0,
            y=0.0,
            position_variance=measurement_variance,
            velocity_variance=KALMAN_INITIAL_VELOCITY_SIGMA_MPS**2,
            cruise_process_noise=KALMAN_CRUISE_PROCESS_NOISE,
            maneuver_process_noise=KALMAN_MANEUVER_PROCESS_NOISE,
        )
        _save_filter(track.id, detection.latitude, detection.longitude, imm, detection.timestamp)
        _apply_filter_to_track(track, imm, detection.latitude, detection.longitude)
    logger.info(
        "New track %s from sensor=%s confidence=%.2f",
        track.track_uid, detection.sensor_id, detection.confidence,
    )
    return track


def _update_track_with_match(detection: Detection, match: _Match, measurement_variance: float) -> Track:
    track, imm, ref_lat, ref_lon = match.track, match.imm, match.ref_lat, match.ref_lon
    zx, zy = latlon_to_local_m(detection.latitude, detection.longitude, ref_lat, ref_lon)
    imm.update(zx, zy, measurement_variance)
    _save_filter(track.id, ref_lat, ref_lon, imm, detection.timestamp)
    _apply_filter_to_track(track, imm, ref_lat, ref_lon)

    track.last_seen = detection.timestamp
    track.altitude_m = detection.altitude_m
    logger.debug("Detection from sensor=%s associated with track %s", detection.sensor_id, track.track_uid)
    return track


def _commit_detection(detection: Detection, track: Track) -> Detection:
    """Persist a detection against its (already updated-in-memory) track,
    fuse classification from the track's history, persist the track, and
    check for zone incidents. Shared by both the single-detection and
    batch association paths.
    """
    # Persist the detection before fusing classification, so this
    # detection's own vote is included in the track's history.
    detection.track_id = track.id
    persisted = create_detection(detection)

    # Multi-sensor classification fusion (app/fusion.py): the fused label
    # from every recent detection decides, not just this one. A detection
    # can always upgrade a track to DRONE (except away from the trusted
    # ADS-B AIRCRAFT label), since misidentifying a real drone as a
    # bird/unknown/friendly and never re-flagging it is the unsafe failure
    # mode. Any other non-UNKNOWN label only fills in a still-unknown
    # classification, never overwrites one.
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


def associate_detection(detection: Detection) -> Detection:
    """Gate an incoming detection against existing tracks, update or spawn a
    track, persist the detection against it, and return the stored detection.

    Association here is greedy nearest-match: this single detection is
    resolved against tracks in isolation, without seeing any other
    detection that might arrive in the same instant. For simultaneous
    detections from one sensor's scan/sweep where two tracks are close
    together, that can swap which detection goes to which track --
    see associate_detections_batch for a joint (global nearest neighbor)
    resolution across a whole batch at once.
    """
    detection = georeference(detection)
    measurement_variance = _measurement_variance(detection.confidence)

    with _association_lock:
        expire_stale_tracks(detection.timestamp)

        match = None
        if detection.latitude is not None and detection.longitude is not None:
            match = _find_matching_track(detection, measurement_variance)

        track = (
            _spawn_track(detection, measurement_variance)
            if match is None
            else _update_track_with_match(detection, match, measurement_variance)
        )
        return _commit_detection(detection, track)


def associate_detections_batch(detections: list[Detection]) -> list[Detection]:
    """Jointly resolve a batch of simultaneous detections against active
    tracks via global nearest neighbor (Hungarian assignment on squared
    Mahalanobis distance) instead of resolving each detection in
    isolation. This is what a single sensor scan/sweep producing several
    plots at once (the normal case for radar) should be posted through
    when two tracks are near each other: per-detection greedy assignment
    can't see a detection's better fit against a *different* track until
    it has already committed the first one, and can swap identities on
    crossing tracks as a result; joint assignment sees the whole batch at
    once and can't make that mistake.

    Gating is identical to associate_detection's (coarse prefilter, then
    squared-Mahalanobis chi-square gate); a detection with no track within
    gate spawns a new track, same as the single-detection path.

    Scope note: this assumes each track contributes at most one detection
    per batch (standard for a single sensor's scan). Don't mix multiple
    sensors' simultaneous reports of the same object into one batch call
    expecting both to land on that object's track -- only one can, by
    construction of a one-to-one assignment; post each sensor's scan as
    its own batch (or single detections) instead.
    """
    if not detections:
        return []

    georeferenced = [georeference(d) for d in detections]
    measurement_variances = [_measurement_variance(d.confidence) for d in georeferenced]

    with _association_lock:
        expire_stale_tracks(max(d.timestamp for d in georeferenced))

        active_tracks = [
            t for t in list_tracks(status=TrackStatus.ACTIVE.value)
            if t.id is not None and t.latitude is not None and t.longitude is not None
        ]
        track_states = {t.id: get_kalman_state(t.id) for t in active_tracks}
        active_tracks = [t for t in active_tracks if track_states[t.id] is not None]

        n, m = len(georeferenced), len(active_tracks)
        cost_matrix: list[list[float]] = []
        # predicted_imm[i][track_id] -> that track's IMM, already predicted
        # forward to detection i's timestamp, ready for update() if chosen.
        predicted_imm: list[dict[int, IMMFilter]] = []

        for i, detection in enumerate(georeferenced):
            row = [_ASSIGNMENT_BLOCKED_COST] * m
            candidates: dict[int, IMMFilter] = {}
            if detection.latitude is not None and detection.longitude is not None:
                for j, track in enumerate(active_tracks):
                    if abs(detection.timestamp - track.last_seen) > timedelta(seconds=TRACK_TIME_GATE_SECONDS):
                        continue
                    coarse_distance = haversine_distance_m(
                        detection.latitude, detection.longitude, track.latitude, track.longitude
                    )
                    if coarse_distance > TRACK_DISTANCE_GATE_M:
                        continue

                    state = track_states[track.id]
                    imm = _load_filter(state)
                    dt_s = (detection.timestamp - state.updated_at).total_seconds()
                    imm.predict(dt_s)
                    zx, zy = latlon_to_local_m(
                        detection.latitude, detection.longitude, state.ref_lat, state.ref_lon
                    )
                    mahalanobis_sq = imm.mahalanobis_sq(zx, zy, measurement_variances[i])
                    if mahalanobis_sq <= TRACK_GATE_CHI2:
                        row[j] = mahalanobis_sq
                        candidates[track.id] = imm
            # One dummy "decline to match" column per detection, at the
            # gate-threshold cost -- lets Hungarian leave a detection
            # unmatched (spawning a new track) exactly when no real track
            # is a better (lower-cost) fit, without needing a hard
            # infeasibility concept in the solver.
            dummy_row = [_ASSIGNMENT_BLOCKED_COST] * n
            dummy_row[i] = TRACK_GATE_CHI2
            cost_matrix.append(row + dummy_row)
            predicted_imm.append(candidates)

        assignment = hungarian_min_cost(cost_matrix)

        results: list[Detection] = []
        for i, detection in enumerate(georeferenced):
            col = assignment[i]
            if col < m and cost_matrix[i][col] < _ASSIGNMENT_BLOCKED_COST:
                chosen_track = active_tracks[col]
                state = track_states[chosen_track.id]
                imm = predicted_imm[i][chosen_track.id]
                match = _Match(chosen_track, imm, state.ref_lat, state.ref_lon)
                track = _update_track_with_match(detection, match, measurement_variances[i])
            else:
                track = _spawn_track(detection, measurement_variances[i])
            results.append(_commit_detection(detection, track))

        return results
