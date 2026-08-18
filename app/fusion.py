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


def _detection_label(detection: Detection) -> Classification:
    if is_authorized_detection(detection):
        return Classification.FRIENDLY
    return classify(detection.sensor_type, detection.confidence)


def fuse_classification(detections: list[Detection]) -> Classification:
    """Weighted vote across every detection: each casts a vote for its own
    label, weighted by (sensor trust * confidence). UNKNOWN votes don't
    count towards any label winning -- they just contribute no evidence.
    Returns UNKNOWN if there's no non-UNKNOWN evidence at all.
    """
    weights: dict[Classification, float] = {}
    for detection in detections:
        label = _detection_label(detection)
        if label == Classification.UNKNOWN:
            continue
        weight = SENSOR_TRUST.get(detection.sensor_type, 1.0) * detection.confidence
        weights[label] = weights.get(label, 0.0) + weight

    if not weights:
        return Classification.UNKNOWN
    return max(weights, key=weights.get)
