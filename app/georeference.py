"""Converts a sensor-relative azimuth/range detection (what a fixed radar
or RF direction-finder typically reports) into an absolute lat/lon, using
the sensor's registered mounting position and orientation (see
app.db.sensor_registry / POST /api/sensor-registrations).

Detections that already carry lat/lon pass through untouched -- most
sensor types (GPS-tagged camera, ADS-B, an already-georeferenced feed)
never need this.
"""

from __future__ import annotations

from app.db import get_sensor_registration
from app.geo import destination_point
from app.models import Detection


def georeference(detection: Detection) -> Detection:
    if detection.latitude is not None and detection.longitude is not None:
        return detection
    if detection.azimuth_deg is None or detection.range_m is None:
        return detection

    registration = get_sensor_registration(detection.sensor_id)
    if registration is None:
        return detection

    bearing_deg = (registration["azimuth_reference_deg"] + detection.azimuth_deg) % 360.0
    latitude, longitude = destination_point(
        registration["latitude"], registration["longitude"], bearing_deg, detection.range_m
    )
    detection.latitude = latitude
    detection.longitude = longitude
    if detection.altitude_m is None and registration["altitude_m"] is not None:
        detection.altitude_m = registration["altitude_m"]
    return detection
