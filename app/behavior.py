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
    positioned = sorted(
        (d for d in detections if d.latitude is not None and d.longitude is not None),
        key=lambda d: d.timestamp,
    )
    if len(positioned) < 2:
        return False

    # Grow the window backward from the latest detection until it spans at
    # least min_duration_s -- NOT "detections within the last
    # min_duration_s," which by definition can never span more than
    # min_duration_s and would make the span_s >= min_duration_s check
    # below nearly always fail on real (non-exactly-boundary-aligned) data.
    latest = positioned[-1]
    window: list[Detection] = []
    reached_min_duration = False
    for detection in reversed(positioned):
        window.append(detection)
        if (latest.timestamp - detection.timestamp).total_seconds() >= min_duration_s:
            reached_min_duration = True
            break
    if not reached_min_duration or len(window) < 2:
        return False

    centroid_lat = sum(d.latitude for d in window) / len(window)
    centroid_lon = sum(d.longitude for d in window) / len(window)
    return all(
        haversine_distance_m(d.latitude, d.longitude, centroid_lat, centroid_lon) <= radius_m
        for d in window
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
    detections_a = sorted(
        (d for d in track_a_detections if d.latitude is not None), key=lambda d: d.timestamp
    )
    detections_b = sorted(
        (d for d in track_b_detections if d.latitude is not None), key=lambda d: d.timestamp
    )
    if len(detections_a) < 2 or len(detections_b) < 2:
        return False

    close_times = []
    for da in detections_a:
        nearest_b = min(detections_b, key=lambda db: abs((db.timestamp - da.timestamp).total_seconds()))
        if abs((nearest_b.timestamp - da.timestamp).total_seconds()) > max_time_gap_s:
            continue
        if haversine_distance_m(da.latitude, da.longitude, nearest_b.latitude, nearest_b.longitude) <= max_distance_m:
            close_times.append(da.timestamp)

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
        track
        for track in tracks
        if track.latitude is not None
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
            track_a, track_b = candidates[i], candidates[j]
            if haversine_distance_m(track_a.latitude, track_a.longitude, track_b.latitude, track_b.longitude) > max_spacing_m:
                continue
            if _heading_difference_deg(track_a.heading_deg, track_b.heading_deg) > heading_tolerance_deg:
                continue
            if abs(track_a.speed_mps - track_b.speed_mps) > speed_tolerance_mps:
                continue
            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(candidates[i].id)

    return [group for group in groups.values() if len(group) >= 2]
