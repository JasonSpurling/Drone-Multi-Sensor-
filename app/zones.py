"""Zone loading (from a JSON file) and point-in-polygon membership tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.db import create_zone, get_zone_by_name, list_zones
from app.models import Zone

DEFAULT_SEED_PATH = Path(__file__).resolve().parent / "zones.seed.json"


def load_zones_from_file(path: Path = DEFAULT_SEED_PATH) -> list[Zone]:
    """Insert any zones from the JSON file that aren't already in the database
    (matched by name), so this is safe to call on every startup.
    """
    if not path.exists():
        return []

    loaded: list[Zone] = []
    for raw in json.loads(path.read_text()):
        zone = Zone.model_validate(raw)
        if get_zone_by_name(zone.name) is None:
            zone = create_zone(zone)
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


def zones_containing_point(lat: float, lon: float) -> list[Zone]:
    return [
        zone
        for zone in list_zones(active_only=True)
        if point_in_polygon(lat, lon, zone.polygon)
    ]
