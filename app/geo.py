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


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Standard great-circle initial bearing (compass degrees, 0-360,
    clockwise from north) from (lat1, lon1) toward (lat2, lon2) -- the
    inverse problem to destination_point (position + bearing + distance ->
    new position); this is two positions -> bearing.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def slant_range_to_ground_range_m(slant_range_m: float, height_diff_m: float) -> float:
    """A radar's reported range (e.g. ASTERIX CAT048 item 040's RHO -- see
    app/adapters/asterix.py) is *slant range*: straight-line distance to
    the target, not the horizontal ground distance destination_point
    actually needs. For a target much higher than the radar and not too
    far away, treating slant range as ground range measurably overplaces
    it beyond its true position (e.g. a target 150m above the radar at
    500m slant range is really only ~477m away over the ground -- a ~5%
    error, worse at closer range/steeper look angles, negligible at long
    range). `height_diff_m` is target altitude minus sensor altitude
    (either sign).

    Falls back to the slant range unchanged (rather than raising) if
    height_diff_m is geometrically inconsistent with slant_range_m (e.g.
    from noisy/independently-sourced altitude data) -- an impossible
    right triangle shouldn't crash georeferencing, and slant range is
    still the best available estimate at that point.
    """
    if abs(height_diff_m) >= slant_range_m:
        return slant_range_m
    return math.sqrt(slant_range_m**2 - height_diff_m**2)


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
