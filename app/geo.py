"""Geodesy helpers: great-circle distance and a local tangent-plane
(equirectangular) projection used to give the Kalman filter a flat,
metric coordinate frame to work in.

The tangent-plane approximation is accurate to well under 1% error at the
tactical ranges (single-digit km) this tracker operates over; it would need
replacing with a proper geodesic projection for long-range tracks.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def latlon_to_local_m(
    lat: float, lon: float, ref_lat: float, ref_lon: float
) -> tuple[float, float]:
    """Project (lat, lon) to local (east_m, north_m) meters relative to a
    fixed reference point.
    """
    north_m = math.radians(lat - ref_lat) * EARTH_RADIUS_M
    east_m = math.radians(lon - ref_lon) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat))
    return east_m, north_m


def local_m_to_latlon(
    east_m: float, north_m: float, ref_lat: float, ref_lon: float
) -> tuple[float, float]:
    """Inverse of latlon_to_local_m."""
    lat = ref_lat + math.degrees(north_m / EARTH_RADIUS_M)
    lon = ref_lon + math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(ref_lat))))
    return lat, lon


def destination_point(
    lat: float, lon: float, bearing_deg: float, distance_m: float
) -> tuple[float, float]:
    """Forward geodesic (spherical-earth): the point reached traveling
    distance_m meters from (lat, lon) along compass bearing_deg. Used to
    georeference a sensor-relative azimuth/range detection into an
    absolute lat/lon given the sensor's known position (see
    app/georeference.py).
    """
    angular_distance = distance_m / EARTH_RADIUS_M
    bearing_rad = math.radians(bearing_deg)
    lat1_rad = math.radians(lat)

    lat2_rad = math.asin(
        math.sin(lat1_rad) * math.cos(angular_distance)
        + math.cos(lat1_rad) * math.sin(angular_distance) * math.cos(bearing_rad)
    )
    lon2_rad = math.radians(lon) + math.atan2(
        math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(lat1_rad),
        math.cos(angular_distance) - math.sin(lat1_rad) * math.sin(lat2_rad),
    )
    return math.degrees(lat2_rad), math.degrees(lon2_rad)
