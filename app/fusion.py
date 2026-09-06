"""Multi-sensor classification fusion: combines every stored detection for
a track into one classification via confidence-and-sensor-trust-weighted
voting, instead of letting only the single most recent detection decide.

This is not machine learning -- it's a transparent, tunable weighted-vote
rule -- but it's a real improvement over "the last non-unknown label
wins": one noisy low-trust detection can no longer flip a track's
classification on its own; it has to outweigh the accumulated evidence
from more trustworthy sensors.
"""

from __future__ import annotations

from datetime import datetime

from app.allowlist import is_authorized_detection
from app.classification import classify
from app.config import (
    CLASSIFICATION_CONFIDENCE_DECAY_SECONDS,
    CLASSIFICATION_CONFIDENCE_FLOOR,
    FUSION_HISTORY_LIMIT,
    INCIDENT_CORROBORATION_MIN_SENSOR_TYPES,
)
from app.db import list_recent_detections
from app.ml.model import predict as ml_predict
from app.models import Classification, Detection, SensorType, Track
from app.rf_signatures import match_rf_signature

# Relative trust per sensor type when weighting its vote against others'.
# ADS-B is a cooperative transponder signal (highest trust); acoustic is
# the least discriminating on its own.
SENSOR_TRUST = {
    SensorType.ADSB: 5.0,
    SensorType.RADAR: 2.0,
    SensorType.RF: 1.5,
    SensorType.CAMERA: 1.0,
    SensorType.ACOUSTIC: 0.5,
    SensorType.OTHER: 1.0,
}


def _effective_confidence(detection: Detection) -> float:
    """The confidence used to classify and weight this detection's vote --
    normally just the sensor's own reported confidence, but for an RF
    detection whose raw_data carries frequency/bandwidth (and optionally
    hopping) fields, boosted to whichever is higher between that and a
    matched signature's confidence (app/rf_signatures.py). A recognized
    drone-control-link RF envelope is real evidence independent of
    whatever confidence the sensor itself reported.
    """
    if detection.sensor_type != SensorType.RF or not detection.raw_data:
        return detection.confidence
    match = match_rf_signature(
        center_frequency_mhz=detection.raw_data.get("center_frequency_mhz"),
        bandwidth_mhz=detection.raw_data.get("bandwidth_mhz"),
        frequency_hopping=detection.raw_data.get("frequency_hopping"),
    )
    return max(detection.confidence, match.confidence)


def _detection_label(detection: Detection, effective_confidence: float) -> Classification:
    if is_authorized_detection(detection):
        return Classification.FRIENDLY
    # An optional first opinion (see app/ml/) -- None whenever
    # DRONE_ML_MODEL_PATH isn't configured (the default), so this is a
    # no-op falling straight through to the rule-based classifier below
    # for every deployment that hasn't opted in.
    ml_label = ml_predict(detection)
    if ml_label is not None:
        return ml_label
    return classify(detection.sensor_type, effective_confidence)


def _classification_weights(detections: list[Detection]) -> dict[Classification, float]:
    """Each detection casts a vote for its own label, weighted by (sensor
    trust * effective confidence). UNKNOWN votes don't count towards any
    label -- they just contribute no evidence. Shared by fuse_classification
    (which label wins) and classification_confidence (how strongly current
    evidence backs a *specific* label, win or not).
    """
    weights: dict[Classification, float] = {}
    for detection in detections:
        effective_confidence = _effective_confidence(detection)
        label = _detection_label(detection, effective_confidence)
        if label == Classification.UNKNOWN:
            continue
        weight = SENSOR_TRUST.get(detection.sensor_type, 1.0) * effective_confidence
        weights[label] = weights.get(label, 0.0) + weight
    return weights


def fuse_classification(detections: list[Detection]) -> Classification:
    """Weighted vote across every detection -- see _classification_weights.
    Returns UNKNOWN if there's no non-UNKNOWN evidence at all.
    """
    weights = _classification_weights(detections)
    if not weights:
        return Classification.UNKNOWN
    return max(weights, key=lambda label: weights[label])


def classification_confidence(detections: list[Detection], label: Classification) -> float | None:
    """How strongly the current evidence backs `label` specifically (0-1)
    -- not necessarily the winning label. app.tracking's upgrade-only
    ratchet (_commit_detection) means a track's *stored* classification
    can outlive contradicting evidence by design (misclassifying a real
    drone as a bird and never re-flagging it is the failure mode that
    ratchet exists to prevent) -- but that doesn't mean the evidence for
    it can't get weaker. This measures confidence in whatever label is
    actually stored, which can legitimately fall even while the label
    itself never downgrades: e.g. a track marked DRONE early on, whose
    more recent detections increasingly look like BIRD, keeps its DRONE
    label but shows falling confidence in it -- an honest signal instead
    of either silently downgrading (the failure mode above) or pretending
    nothing changed.

    None for UNKNOWN -- there's no meaningful "confidence in not knowing".
    """
    if label == Classification.UNKNOWN:
        return None
    weights = _classification_weights(detections)
    total = sum(weights.values())
    if total <= 0:
        return None
    return weights.get(label, 0.0) / total


def decay_classification_confidence(raw_confidence: float | None, last_seen: datetime, now: datetime) -> float | None:
    """Applies read-time staleness decay to a track's stored
    classification_confidence (the fused vote-share as of its last
    detection, computed by classification_confidence above and persisted
    on the track) -- linearly toward CLASSIFICATION_CONFIDENCE_FLOOR as
    `now` moves past `last_seen`, fully decayed by
    CLASSIFICATION_CONFIDENCE_DECAY_SECONDS. Pure function of elapsed
    time, computed at read time (app/api/tracks.py) rather than a stored,
    actively-updated value -- there's no background job that could ever
    go stale itself, and it's always correct for whatever "now" the
    caller asks from.
    """
    if raw_confidence is None:
        return None
    age_s = max(0.0, (now - last_seen).total_seconds())
    decay_fraction = min(1.0, age_s / CLASSIFICATION_CONFIDENCE_DECAY_SECONDS)
    return raw_confidence + (CLASSIFICATION_CONFIDENCE_FLOOR - raw_confidence) * decay_fraction


def contributing_sensor_types(track: Track) -> set[str]:
    """Which distinct sensor types have reported on this track within the
    same recent-detection window this module's own classification fusion
    considers (FUSION_HISTORY_LIMIT) -- e.g. {"radar", "camera"}. Shared
    by app.incidents (severity escalation, via the count below), GET
    /api/tracks (the dashboard's Verified/Unverified grouping and its
    sensor-coverage display), so all three mean the same thing by
    "corroborated" rather than keeping separate copies of this query that
    could quietly drift apart.
    """
    if track.id is None or track.site_id is None:
        return set()
    history = list_recent_detections(track.id, track.site_id, FUSION_HISTORY_LIMIT)
    return {d.sensor_type for d in history}


def corroborating_sensor_type_count(track: Track) -> int:
    """len(contributing_sensor_types(track)) -- kept as its own function
    since most callers (severity escalation, the verified/unverified
    threshold) only ever need the count, not the actual set.
    """
    return len(contributing_sensor_types(track))


def is_verified(track: Track) -> bool:
    """A track is "Verified" once INCIDENT_CORROBORATION_MIN_SENSOR_TYPES
    (default 2) distinct sensor types have independently reported on it
    -- the same corroboration threshold app.incidents already uses to
    escalate severity, not a second, differently-tuned definition of
    "verified" invented just for the dashboard's grouping.
    """
    return corroborating_sensor_type_count(track) >= INCIDENT_CORROBORATION_MIN_SENSOR_TYPES
