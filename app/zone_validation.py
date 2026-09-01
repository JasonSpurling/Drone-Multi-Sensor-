"""Structural validation for a zone polygon, independent of (and stricter
than) the field-level checks Zone.polygon itself gets from Pydantic (each
vertex is a float pair, nothing about the shape they form together). A
polygon can pass that and still be geometrically broken for
app.zones.point_in_polygon's ray-casting test: too few vertices to
enclose any area, a vertex outside real lat/lon range, or self-
intersecting edges (a "bowtie" shape, where two non-adjacent edges cross)
-- ray-casting doesn't error on a self-intersecting polygon, it just
silently gives a wrong inside/outside answer for points near the
crossing, which is far harder to notice than an upfront rejection.

Used by scripts/validate_zone.py (a CLI to check a hand-written or
drawn polygon before it goes into zones.seed.json or POST /api/zones).
"""

from __future__ import annotations

Point = tuple[float, float]


def _orientation(p: Point, q: Point, r: Point) -> int:
    """0 = collinear, 1 = clockwise, 2 = counterclockwise, for the turn
    p -> q -> r. Standard cross-product sign test.
    """
    val = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
    if val == 0:
        return 0
    return 1 if val > 0 else 2


def _on_segment(p: Point, q: Point, r: Point) -> bool:
    """True if q lies on segment p-r, given p, q, r are already known collinear."""
    return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])


def _segments_intersect(seg1: tuple[Point, Point], seg2: tuple[Point, Point]) -> bool:
    """Standard orientation-based segment-intersection test (handles the
    collinear/touching edge cases explicitly, not just the general case).
    """
    p1, q1 = seg1
    p2, q2 = seg2
    o1 = _orientation(p1, q1, p2)
    o2 = _orientation(p1, q1, q2)
    o3 = _orientation(p2, q2, p1)
    o4 = _orientation(p2, q2, q1)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p2, q1):
        return True
    if o2 == 0 and _on_segment(p1, q2, q1):
        return True
    if o3 == 0 and _on_segment(p2, p1, q2):
        return True
    return bool(o4 == 0 and _on_segment(p2, q1, q2))


def validate_polygon(polygon: list[Point]) -> list[str]:
    """Returns a list of human-readable problems with `polygon`, empty if
    it's structurally valid. Never raises -- a malformed polygon is
    exactly the expected input for a validator, not an error condition.
    """
    errors: list[str] = []

    if len(polygon) < 3:
        errors.append(f"needs at least 3 vertices, got {len(polygon)}")
        return errors  # nothing else below is meaningful to check yet

    for i, (lat, lon) in enumerate(polygon):
        if not (-90.0 <= lat <= 90.0):
            errors.append(f"vertex {i}: latitude {lat} out of range [-90, 90]")
        if not (-180.0 <= lon <= 180.0):
            errors.append(f"vertex {i}: longitude {lon} out of range [-180, 180]")
    if errors:
        # Out-of-range coordinates make the geometry checks below
        # meaningless (they'd just report a symptom of the same root
        # cause) -- report the real problem once, not a cascade of
        # derived ones.
        return errors

    n = len(polygon)
    for i in range(n):
        if polygon[i] == polygon[(i + 1) % n]:
            errors.append(f"duplicate consecutive vertex at index {i}: {polygon[i]}")

    edges = [(polygon[i], polygon[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        # j > i (unordered pairs only) and never adjacent -- adjacent
        # edges always share exactly one endpoint, which is a normal,
        # valid polygon shape, not a self-intersection.
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_intersect(edges[i], edges[j]):
                errors.append(f"self-intersecting: edge {i} ({polygon[i]}->{polygon[(i + 1) % n]}) "
                              f"crosses edge {j} ({polygon[j]}->{polygon[(j + 1) % n]})")

    return errors
