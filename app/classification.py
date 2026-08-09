"""Simple sensor-type + confidence classification rules."""

from __future__ import annotations

from app.config import BIRD_CONFIDENCE_THRESHOLD, DRONE_CONFIDENCE_THRESHOLD
from app.models import Classification, SensorType


def classify(sensor_type: SensorType, confidence: float) -> Classification:
    """Label a single detection from its sensor type and confidence.

    ADS-B is a cooperative transponder signal, so it is trusted as AIRCRAFT
    outright. Otherwise a high-confidence return is treated as a drone; a
    low-confidence optical/acoustic return is more likely a bird than a drone.
    """
    if sensor_type == SensorType.ADSB:
        return Classification.AIRCRAFT
    if confidence >= DRONE_CONFIDENCE_THRESHOLD:
        return Classification.DRONE
    if sensor_type in (SensorType.CAMERA, SensorType.ACOUSTIC) and confidence < BIRD_CONFIDENCE_THRESHOLD:
        return Classification.BIRD
    return Classification.UNKNOWN
