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
from app.cluster_lock import cluster_association_lock
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
from app.cot_publisher import publish_track_cot
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
from app.fusion import classification_confidence, fuse_classification
from app.geo import haversine_distance_m, latlon_to_local_m, local_m_to_latlon
from app.georeference import georeference
from app.imm import IMMFilter
from app.incidents import (
    check_loitering_incident,
    check_predicted_incursions,
    check_zone_incident_resolutions,
    check_zone_incidents,
    close_incidents_for_closed_track,
)
from app.kalman import ConstantVelocityKalmanFilter
from app.live import publish as publish_live_event
from app.models import Classification, Detection, Track, TrackStatus
from app.queue_publisher import publish_detection
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


def expire_stale_tracks(site_id: int, now: datetime | None = None) -> None:
    """Sweep tracks: active -> lost -> closed once each has gone quiet too long."""
    now = now or utcnow()

    for track in list_tracks(site_id=site_id, status=TrackStatus.ACTIVE.value):
        if now - track.last_seen > timedelta(seconds=TRACK_STALE_SECONDS):
            track.status = TrackStatus.LOST
            update_track(track)
            logger.info("Track %s -> lost (last seen %s)", track.track_uid, track.last_seen)

    for track in list_tracks(site_id=site_id, status=TrackStatus.LOST.value):
        if now - track.last_seen > timedelta(seconds=TRACK_DROP_SECONDS):
            track.status = TrackStatus.CLOSED
            update_track(track)
            logger.info("Track %s -> closed (last seen %s)", track.track_uid, track.last_seen)
            close_incidents_for_closed_track(track)


def _coarse_distance_gate_m(track: Track, elapsed_s: float) -> float:
    """TRACK_DISTANCE_GATE_M widened by however far the track's last known
    ground speed could have carried it since track.last_seen. Without this,
    the coarse prefilter compares against the track's raw last-known
    position (not the IMM-predicted one, which needs a DB round-trip this
    prefilter exists to avoid for the common far-away case) using a fixed
    threshold -- so a fast-moving or infrequently-updated track can drift
    outside a fixed 500m gate well before the Mahalanobis/IMM math (which
    does account for that via prediction) ever gets to see it, silently
    spawning a duplicate track instead of matching the real one. Bounding
    by speed * elapsed time is a safe upper bound regardless of heading
    change in between (a turn's arc length can't exceed straight-line
    speed * time, so this never over-widens enough to let a genuinely
    distant/different object slip past the coarse filter).
    """
    speed_mps = track.speed_mps or 0.0
    return TRACK_DISTANCE_GATE_M + speed_mps * max(elapsed_s, 0.0)


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

    if detection.site_id is None:
        return None

    best: tuple[_Match, float] | None = None
    for track in list_tracks(site_id=detection.site_id, status=TrackStatus.ACTIVE.value):
        if track.id is None or track.latitude is None or track.longitude is None:
            continue
        elapsed_s = (detection.timestamp - track.last_seen).total_seconds()
        if abs(elapsed_s) > TRACK_TIME_GATE_SECONDS:
            continue

        # Cheap coarse prefilter before the more expensive Mahalanobis math.
        coarse_distance = haversine_distance_m(
            detection.latitude, detection.longitude, track.latitude, track.longitude
        )
        if coarse_distance > _coarse_distance_gate_m(track, elapsed_s):
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
    if detection.site_id is None:
        raise ValueError("_spawn_track requires detection.site_id to be set")
    track = create_track(
        Track(
            site_id=detection.site_id,
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
        assert track.id is not None  # just persisted by create_track above
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
    # Both callers only build a _Match when the detection has a resolved
    # position (associate_detection checks before calling
    # _find_matching_track; associate_detections_batch's cost-matrix loop
    # is itself gated the same way) and from an active_tracks list already
    # filtered to a persisted id.
    assert detection.latitude is not None and detection.longitude is not None
    track, imm, ref_lat, ref_lon = match.track, match.imm, match.ref_lat, match.ref_lon
    assert track.id is not None
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
    # track is always either freshly spawned (_spawn_track, just persisted)
    # or an existing match (_update_track_with_match, already asserted).
    assert track.id is not None
    # Persist the detection before fusing classification, so this
    # detection's own vote is included in the track's history.
    detection.track_id = track.id
    persisted = create_detection(detection)
    publish_detection(persisted.model_dump(mode="json"))

    # Multi-sensor classification fusion (app/fusion.py): the fused label
    # from every recent detection decides, not just this one. A detection
    # can always upgrade a track to DRONE (except away from the trusted
    # ADS-B AIRCRAFT label), since misidentifying a real drone as a
    # bird/unknown/friendly and never re-flagging it is the unsafe failure
    # mode. Any other non-UNKNOWN label only fills in a still-unknown
    # classification, never overwrites one.
    assert track.site_id is not None
    history = list_recent_detections(track.id, track.site_id, FUSION_HISTORY_LIMIT)
    fused_label = fuse_classification(history)
    upgrades_to_drone = fused_label == Classification.DRONE and track.classification != Classification.AIRCRAFT
    fills_in_unknown = track.classification == Classification.UNKNOWN and fused_label != Classification.UNKNOWN
    if upgrades_to_drone or fills_in_unknown:
        track.classification = fused_label
    # How strongly *current* evidence backs whatever ended up stored above
    # (app.fusion.classification_confidence's docstring) -- deliberately
    # decoupled from the upgrade-only ratchet just above: the label can
    # only ever move toward DRONE/fill in from UNKNOWN, but confidence in
    # it is free to rise and fall with the actual evidence, e.g. a track
    # that's DRONE from an early high-confidence reading but has since
    # only gathered bird-like evidence shows falling confidence without
    # ever silently losing its DRONE label.
    track.classification_confidence = classification_confidence(history, track.classification)
    # A real ADS-B emitter category (app.models.Track.aircraft_category's
    # docstring) is a transponder-reported fact, not a threat judgment --
    # unlike classification above, the latest report simply wins rather
    # than needing an "upgrade only" guard.
    reported_category = (detection.raw_data or {}).get("category")
    if reported_category:
        track.aircraft_category = reported_category
    update_track(track)
    publish_track_cot(track)
    publish_live_event(track.site_id, {"type": "track_update", "track": track.model_dump(mode="json")})

    check_zone_incidents(track)
    check_zone_incident_resolutions(track)
    check_predicted_incursions(track)
    check_loitering_incident(track)

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
    if detection.site_id is None:
        raise ValueError("associate_detection requires detection.site_id to be set")
    site_id: int = detection.site_id
    detection = georeference(detection)
    measurement_variance = _measurement_variance(detection.confidence)

    with _association_lock, cluster_association_lock():
        expire_stale_tracks(site_id, detection.timestamp)

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
    site_ids = {d.site_id for d in detections}
    if site_ids == {None}:
        raise ValueError("associate_detections_batch requires every detection.site_id to be set")
    if len(site_ids) > 1:
        raise ValueError(
            "associate_detections_batch requires every detection in a batch to share one site_id "
            "-- post each site's detections as its own batch"
        )
    site_id = next(iter(site_ids))
    assert site_id is not None

    georeferenced = [georeference(d) for d in detections]
    measurement_variances = [_measurement_variance(d.confidence) for d in georeferenced]

    with _association_lock, cluster_association_lock():
        expire_stale_tracks(site_id, max(d.timestamp for d in georeferenced))

        active_tracks = [
            t for t in list_tracks(site_id=site_id, status=TrackStatus.ACTIVE.value)
            if t.id is not None and t.latitude is not None and t.longitude is not None
        ]
        # A dict comprehension/list filter's condition narrows t.id within
        # that one expression, but the resulting list is still typed
        # list[Track] (id: int | None) -- the filter above doesn't change
        # that, so each loop below re-asserts it at first use.
        track_states: dict[int, KalmanStateRecord | None] = {}
        for t in active_tracks:
            assert t.id is not None
            track_states[t.id] = get_kalman_state(t.id)
        active_tracks = [t for t in active_tracks if t.id is not None and track_states[t.id] is not None]

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
                    assert track.id is not None and track.latitude is not None and track.longitude is not None
                    elapsed_s = (detection.timestamp - track.last_seen).total_seconds()
                    if abs(elapsed_s) > TRACK_TIME_GATE_SECONDS:
                        continue
                    coarse_distance = haversine_distance_m(
                        detection.latitude, detection.longitude, track.latitude, track.longitude
                    )
                    if coarse_distance > _coarse_distance_gate_m(track, elapsed_s):
                        continue

                    state = track_states[track.id]
                    assert state is not None  # active_tracks was filtered to have a live Kalman state
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
                assert chosen_track.id is not None
                state = track_states[chosen_track.id]
                assert state is not None
                imm = predicted_imm[i][chosen_track.id]
                match = _Match(chosen_track, imm, state.ref_lat, state.ref_lon)
                track = _update_track_with_match(detection, match, measurement_variances[i])
            else:
                track = _spawn_track(detection, measurement_variances[i])
            results.append(_commit_detection(detection, track))

        return results
