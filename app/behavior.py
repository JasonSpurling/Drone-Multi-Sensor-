"""Behavioral pattern-of-life analysis on the track history this app now
records (app/api/tracks.py's history/export endpoints): loitering,
sustained shadowing of another track, and formation/swarm movement --
beyond "is it in a zone," which is the only question app/incidents.py
asked before this.

Pure functions operating on Detection/Track data already available --
no new sensor, no new dependency. See app/incidents.py for how loitering
gets checked per-track on every update, and app/behavior_sweep.py for the
periodic cross-track sweep formation/shadowing need (they compare
multiple tracks against each other, a different shape than a per-track
check).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.geo import haversine_distance_m
from app.models import Detection, Track


def detect_loitering(
    detections: list[Detection], radius_m: float = 75.0, min_duration_s: float = 120.0
) -> bool:
    """True if this track's most recent min_duration_s of detections all
    sit within radius_m of their own centroid -- circling/hovering over
    one spot rather than transiting through. Requires the trailing window
    to actually span at least min_duration_s of real elapsed time (not
    just "the last N points," which could be a burst of closely-spaced
    detections covering a much shorter real interval) -- too little
    history to judge yet returns False, not a guess.
    """
    # (lat, lon, timestamp) instead of the Detection objects themselves --
    # narrows lat/lon to plain floats once, up front, rather than re-
    # asserting them non-None (to the type checker, and in principle) on
    # every centroid/distance computation below.
    positioned = sorted(
        (
            (d.latitude, d.longitude, d.timestamp)
            for d in detections
            if d.latitude is not None and d.longitude is not None
        ),
        key=lambda p: p[2],
    )
    if len(positioned) < 2:
        return False

    # Grow the window backward from the latest detection until it spans at
    # least min_duration_s -- NOT "detections within the last
    # min_duration_s," which by definition can never span more than
    # min_duration_s and would make the span_s >= min_duration_s check
    # below nearly always fail on real (non-exactly-boundary-aligned) data.
    latest_timestamp = positioned[-1][2]
    window: list[tuple[float, float, datetime]] = []
    reached_min_duration = False
    for point in reversed(positioned):
        window.append(point)
        if (latest_timestamp - point[2]).total_seconds() >= min_duration_s:
            reached_min_duration = True
            break
    if not reached_min_duration or len(window) < 2:
        return False

    centroid_lat = sum(p[0] for p in window) / len(window)
    centroid_lon = sum(p[1] for p in window) / len(window)
    return all(
        haversine_distance_m(lat, lon, centroid_lat, centroid_lon) <= radius_m
        for lat, lon, _ in window
    )


def detect_shadowing(
    track_a_detections: list[Detection],
    track_b_detections: list[Detection],
    max_distance_m: float = 30.0,
    min_duration_s: float = 60.0,
    max_time_gap_s: float = 10.0,
) -> bool:
    """True if track A stayed within max_distance_m of track B for a
    contiguous stretch of at least min_duration_s -- one track
    consistently shadowing another, not just two tracks that happened to
    pass close by once. Each of A's detections is paired with B's
    nearest-in-time one (within max_time_gap_s -- sensors rarely report
    at identical instants), since the two tracks' own detections aren't
    necessarily time-aligned.

    Works for any two tracks, not literally "a drone following a
    person" -- this app has no independent person-detection sensor, so it
    can't verify the shadowed target's identity; it's a general escort/
    shadowing pattern between whatever two tracks are being compared.
    """
    # (lat, lon, timestamp) tuples, narrowing lat/lon to plain floats once
    # up front rather than at every nearest-neighbor/distance computation.
    detections_a = sorted(
        (
            (d.latitude, d.longitude, d.timestamp)
            for d in track_a_detections
            if d.latitude is not None and d.longitude is not None
        ),
        key=lambda p: p[2],
    )
    detections_b = sorted(
        (
            (d.latitude, d.longitude, d.timestamp)
            for d in track_b_detections
            if d.latitude is not None and d.longitude is not None
        ),
        key=lambda p: p[2],
    )
    if len(detections_a) < 2 or len(detections_b) < 2:
        return False

    close_times = []
    for da in detections_a:
        nearest_b = min(detections_b, key=lambda db: abs((db[2] - da[2]).total_seconds()))
        if abs((nearest_b[2] - da[2]).total_seconds()) > max_time_gap_s:
            continue
        if haversine_distance_m(da[0], da[1], nearest_b[0], nearest_b[1]) <= max_distance_m:
            close_times.append(da[2])

    if len(close_times) < 2:
        return False

    close_times.sort()
    longest_span_s = 0.0
    run_start = close_times[0]
    previous = close_times[0]
    for t in close_times[1:]:
        if (t - previous).total_seconds() > max_time_gap_s:
            longest_span_s = max(longest_span_s, (previous - run_start).total_seconds())
            run_start = t
        previous = t
    longest_span_s = max(longest_span_s, (previous - run_start).total_seconds())

    return longest_span_s >= min_duration_s


def _heading_difference_deg(heading_a: float, heading_b: float) -> float:
    diff = abs(heading_a - heading_b) % 360.0
    return min(diff, 360.0 - diff)


@dataclass
class _FormationCandidate:
    """A Track narrowed to the fields detect_formations actually needs, all
    guaranteed non-None by construction -- lets the rest of the function
    work with plain floats instead of re-checking Optional fields (and
    re-asserting that to the type checker) on every pairwise comparison.
    """

    id: int
    lat: float
    lon: float
    heading_deg: float
    speed_mps: float


def detect_formations(
    tracks: list[Track],
    max_spacing_m: float = 100.0,
    heading_tolerance_deg: float = 15.0,
    speed_tolerance_mps: float = 2.0,
    min_speed_mps: float = 1.0,
) -> list[list[int]]:
    """Groups active tracks that are spatially close AND moving with
    similar heading/speed -- coordinated formation flight, the pattern a
    swarm shows and a coincidental cluster of unrelated tracks doesn't.
    Returns each formation as a list of track IDs (only groups of 2+;
    singletons aren't a formation). Tracks below min_speed_mps are
    excluded -- two hovering/stationary tracks near each other aren't
    "flying in formation," and near-zero speed makes heading meaningless
    (a Kalman-filtered heading is noisy at near-zero velocity).

    Grouping is transitive (union-find): if A matches B and B matches C,
    all three land in one formation even if A and C aren't within
    max_spacing_m of each other directly -- the same "is this one
    coordinated group" question a chain of nearby, similarly-moving
    tracks answers, not just isolated pairs.
    """
    candidates = [
        _FormationCandidate(track.id, track.latitude, track.longitude, track.heading_deg, track.speed_mps)
        for track in tracks
        # id is None only for a track that's never been persisted; a
        # formation of one can't be turned into an incident anyway (see
        # app/incidents.py's check_formation_incidents, which looks tracks
        # up by id), so it's excluded the same as any other missing field.
        if track.id is not None
        and track.latitude is not None
        and track.longitude is not None
        and track.heading_deg is not None
        and track.speed_mps is not None
        and track.speed_mps >= min_speed_mps
    ]
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for i in range(n):
        for j in range(i + 1, n):
            a, b = candidates[i], candidates[j]
            if haversine_distance_m(a.lat, a.lon, b.lat, b.lon) > max_spacing_m:
                continue
            if _heading_difference_deg(a.heading_deg, b.heading_deg) > heading_tolerance_deg:
                continue
            if abs(a.speed_mps - b.speed_mps) > speed_tolerance_mps:
                continue
            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(candidates[i].id)

    return [group for group in groups.values() if len(group) >= 2]
