"""Zone loading (from a JSON file) and point-in-polygon membership tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import ZONES_SEED_PATH
from app.db import create_zone, get_zone_by_name, list_zones
from app.models import Zone

logger = logging.getLogger(__name__)


def load_zones_from_file(path: Path = ZONES_SEED_PATH) -> list[Zone]:
    """Insert any zones from the JSON file that aren't already in the database
    (matched by name), so this is safe to call on every startup.
    """
    if not path.exists():
        logger.warning("Zone seed file not found: %s", path)
        return []

    loaded: list[Zone] = []
    for raw in json.loads(path.read_text()):
        zone = Zone.model_validate(raw)
        if get_zone_by_name(zone.name) is None:
            zone = create_zone(zone)
            logger.info("Loaded zone '%s' (%s) from seed file", zone.name, zone.zone_type.value)
        loaded.append(zone)
    return loaded


def point_in_polygon(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    """Standard ray-casting point-in-polygon test over (lat, lon) vertices."""
    inside = False
    n = len(polygon)
    x, y = lon, lat
    for i in range(n):
        x1, y1 = polygon[i][1], polygon[i][0]
        x2, y2 = polygon[(i + 1) % n][1], polygon[(i + 1) % n][0]
        if (y1 > y) != (y2 > y):
            x_intersect = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_intersect:
                inside = not inside
    return inside


def _within_altitude_band(altitude_m: float | None, zone: Zone) -> bool:
    """True if altitude_m is within the zone's altitude band, or if either
    is unknown (an unknown altitude can't be used to rule a zone out).
    """
    if altitude_m is None:
        return True
    if zone.min_altitude_m is not None and altitude_m < zone.min_altitude_m:
        return False
    if zone.max_altitude_m is not None and altitude_m > zone.max_altitude_m:
        return False
    return True


def zones_containing_point(lat: float, lon: float, altitude_m: float | None = None) -> list[Zone]:
    return [
        zone
        for zone in list_zones(active_only=True)
        if point_in_polygon(lat, lon, zone.polygon) and _within_altitude_band(altitude_m, zone)
    ]
