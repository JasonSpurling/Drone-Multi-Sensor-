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

from app.allowlist import is_authorized_detection
from app.classification import classify
from app.models import Classification, Detection, SensorType
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
    return classify(detection.sensor_type, effective_confidence)


def fuse_classification(detections: list[Detection]) -> Classification:
    """Weighted vote across every detection: each casts a vote for its own
    label, weighted by (sensor trust * effective confidence). UNKNOWN
    votes don't count towards any label winning -- they just contribute
    no evidence. Returns UNKNOWN if there's no non-UNKNOWN evidence at all.
    """
    weights: dict[Classification, float] = {}
    for detection in detections:
        effective_confidence = _effective_confidence(detection)
        label = _detection_label(detection, effective_confidence)
        if label == Classification.UNKNOWN:
            continue
        weight = SENSOR_TRUST.get(detection.sensor_type, 1.0) * effective_confidence
        weights[label] = weights.get(label, 0.0) + weight

    if not weights:
        return Classification.UNKNOWN
    return max(weights, key=weights.get)
