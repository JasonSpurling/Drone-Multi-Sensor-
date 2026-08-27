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
from app.geo import destination_point, slant_range_to_ground_range_m
from app.models import Detection


def georeference(detection: Detection) -> Detection:
    if detection.latitude is not None and detection.longitude is not None:
        return detection
    if detection.azimuth_deg is None or detection.range_m is None:
        return detection

    if detection.site_id is None:
        return detection
    registration = get_sensor_registration(detection.sensor_id, detection.site_id)
    if registration is None:
        return detection

    bearing_deg = (registration["azimuth_reference_deg"] + detection.azimuth_deg) % 360.0
    # range_m from an azimuth/range sensor (e.g. ASTERIX CAT048's RHO) is
    # slant range, not ground range -- destination_point needs the latter.
    # Only correctable when both ends of the height difference are known;
    # a detection reporting no altitude of its own (most azimuth/range
    # sensors -- acoustic bearing-only arrays, many RF direction finders)
    # falls back to treating range_m as ground range unchanged, same as
    # before this correction existed.
    ground_range_m = detection.range_m
    if detection.altitude_m is not None and registration["altitude_m"] is not None:
        height_diff_m = detection.altitude_m - registration["altitude_m"]
        ground_range_m = slant_range_to_ground_range_m(detection.range_m, height_diff_m)
    latitude, longitude = destination_point(
        registration["latitude"], registration["longitude"], bearing_deg, ground_range_m
    )
    detection.latitude = latitude
    detection.longitude = longitude
    detection.georeferenced = True
    if detection.altitude_m is None and registration["altitude_m"] is not None:
        detection.altitude_m = registration["altitude_m"]
    return detection
