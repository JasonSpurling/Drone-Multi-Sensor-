"""Slew-to-cue: computes the pan/tilt angles a PTZ (pan-tilt-zoom) camera
needs to point at a target detected by a *different* sensor -- the real
sensor-control-loop this app was missing. Every adapter in this app
ingests a static/fixed-position feed; nothing has ever pointed a physical
sensor anywhere. A bearing-only sensor (an RF direction-finder, an
acoustic array, app/acoustic_beamforming.py) or any other sensor's
resolved track position can now cue a PTZ camera mounted somewhere else
entirely to swing onto it, instead of a human operator manually panning
to follow a cue by hand.

Pure geodesy here -- no camera/PTZ-protocol dependency; see
app/adapters/onvif_ptz_bridge.py for the actual hardware control (ONVIF,
the open IP-camera control standard most commercial PTZ cameras support),
kept as an optional extra like every other hardware-facing adapter in
this app.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.geo import haversine_distance_m, initial_bearing_deg


@dataclass(frozen=True)
class CameraCue:
    pan_deg: float
    """Compass bearing (0-360, clockwise from north) from the camera to
    the target -- an absolute heading, independent of how the camera
    happens to be mounted."""

    pan_relative_deg: float
    """pan_deg adjusted for the camera's own mounting orientation
    (azimuth_reference_deg, the same field every other azimuth-reporting
    sensor in this app registers -- see app/georeference.py) -- this is
    the number to actually send a PTZ camera whose pan axis is zeroed to
    its own boresight, not true north."""

    tilt_deg: float
    """Elevation angle (degrees) from the camera to the target: positive
    is above the camera's horizon, negative is below."""

    distance_m: float
    """Great-circle horizontal distance from the camera to the target --
    useful for setting zoom (closer = wider FOV needed, farther =
    tighter), which this module deliberately doesn't prescribe, since
    the right zoom-vs-distance curve is camera/lens specific."""


def compute_camera_cue(
    camera_lat: float,
    camera_lon: float,
    camera_alt_m: float,
    camera_azimuth_reference_deg: float,
    target_lat: float,
    target_lon: float,
    target_alt_m: float | None,
) -> CameraCue:
    """`target_alt_m` of None (the target's altitude isn't known -- e.g. a
    bearing-only acoustic/RF detection with an assumed range and no
    altitude at all) is treated as level with the camera, tilt 0, rather
    than guessing a wrong elevation angle.
    """
    pan_deg = initial_bearing_deg(camera_lat, camera_lon, target_lat, target_lon)
    pan_relative_deg = (pan_deg - camera_azimuth_reference_deg) % 360.0
    distance_m = haversine_distance_m(camera_lat, camera_lon, target_lat, target_lon)

    if target_alt_m is None or distance_m == 0:
        tilt_deg = 0.0
    else:
        height_diff_m = target_alt_m - camera_alt_m
        tilt_deg = math.degrees(math.atan2(height_diff_m, distance_m))

    return CameraCue(
        pan_deg=pan_deg,
        pan_relative_deg=pan_relative_deg,
        tilt_deg=tilt_deg,
        distance_m=distance_m,
    )
