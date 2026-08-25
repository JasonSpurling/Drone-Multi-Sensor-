"""Deterministic feature extraction from a Detection, shared by training
(app/ml/train.py) and inference (app/ml/model.py) so the exact same
transformation runs in both places -- train/serve skew (the model trained
on one feature shape but fed a subtly different one at inference time) is
one of the most common ways a real ML pipeline silently degrades, and
sharing this one function is what rules it out here.
"""

from __future__ import annotations

from app.models import Detection, SensorType


def extract_features(detection: Detection) -> dict[str, float | str]:
    """A flat feature dict, ready for sklearn's DictVectorizer (which
    handles the string sensor_type value as a one-hot categorical and
    every other key as numeric). A feature that doesn't apply to this
    detection (no altitude reported, not an RF detection, ...) is simply
    omitted from the dict rather than set to some sentinel like -1 or
    NaN -- DictVectorizer treats a missing key as 0 for every row, which
    is a reasonable default for "this signal wasn't present" here (a
    trained model can still learn that 0 correlates with "missing" for
    a feature that's never legitimately 0, e.g. rf_bandwidth_mhz).
    """
    features: dict[str, float | str] = {
        "sensor_type": detection.sensor_type.value,
        "confidence": detection.confidence,
    }
    if detection.altitude_m is not None:
        features["altitude_m"] = detection.altitude_m

    raw = detection.raw_data or {}
    if detection.sensor_type == SensorType.RF:
        if "center_frequency_mhz" in raw:
            features["rf_center_frequency_mhz"] = float(raw["center_frequency_mhz"])
        if "bandwidth_mhz" in raw:
            features["rf_bandwidth_mhz"] = float(raw["bandwidth_mhz"])
        if raw.get("frequency_hopping"):
            features["rf_frequency_hopping"] = 1.0

    return features
