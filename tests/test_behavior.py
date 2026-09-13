from datetime import datetime, timedelta

from app.behavior import detect_formations, detect_loitering, detect_shadowing
from app.models import Classification, Detection, SensorType, Track, TrackStatus

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(seconds_offset: float, lat: float, lon: float) -> Detection:
    return Detection(
        sensor_id="s1", sensor_type=SensorType.RADAR,
        timestamp=BASE_TIME + timedelta(seconds=seconds_offset),
        latitude=lat, longitude=lon, confidence=0.9,
    )


def make_track(
    track_id: int, lat: float, lon: float, heading_deg: float | None, speed_mps: float | None
) -> Track:
    return Track(
        id=track_id, track_uid=f"track-{track_id}", first_seen=BASE_TIME, last_seen=BASE_TIME,
        status=TrackStatus.ACTIVE, classification=Classification.DRONE,
        latitude=lat, longitude=lon, heading_deg=heading_deg, speed_mps=speed_mps,
    )


# --- Loitering ---------------------------------------------------------

def test_loitering_detected_when_positions_stay_within_radius():
    detections = [make_detection(t, 51.5, -0.1 + t * 1e-6) for t in range(0, 130, 10)]
    assert detect_loitering(detections, radius_m=50.0, min_duration_s=120.0) is True


def test_no_loitering_when_track_is_transiting():
    # Moving steadily ~110m per 10s tick -> definitely not loitering.
    detections = [make_detection(t, 51.5 + t * 0.001, -0.1) for t in range(0, 130, 10)]
    assert detect_loitering(detections, radius_m=50.0, min_duration_s=120.0) is False


def test_no_loitering_with_insufficient_history_duration():
    # Only 30s of history when 120s is required -- too little to judge.
    detections = [make_detection(t, 51.5, -0.1) for t in range(0, 30, 10)]
    assert detect_loitering(detections, radius_m=50.0, min_duration_s=120.0) is False


def test_no_loitering_with_fewer_than_two_positioned_detections():
    assert detect_loitering([make_detection(0, 51.5, -0.1)], min_duration_s=120.0) is False
    assert detect_loitering([], min_duration_s=120.0) is False


def test_loitering_detected_with_sparse_unevenly_aligned_spacing():
    # Regression test: an earlier version filtered to "detections within
    # the last min_duration_s" and then required THAT window's span to be
    # >= min_duration_s -- a window capped at min_duration_s can never
    # exceed it, so that check nearly always failed except at an exact
    # boundary alignment. Caught via a live end-to-end smoke test (8s
    # spacing against a 30s threshold), not by the tests above, which
    # happened to use spacing/thresholds that didn't expose it.
    detections = [make_detection(t, 51.5, -0.1) for t in range(0, 41, 8)]  # 0,8,16,24,32,40
    assert detect_loitering(detections, radius_m=50.0, min_duration_s=30.0) is True


def test_loitering_ignores_detections_without_a_resolved_position():
    detections = [make_detection(t, 51.5, -0.1) for t in range(0, 130, 10)]
    detections.append(
        Detection(sensor_id="s2", sensor_type=SensorType.RF, timestamp=BASE_TIME, azimuth_deg=10.0, confidence=0.5)
    )
    assert detect_loitering(detections, radius_m=50.0, min_duration_s=120.0) is True


# --- Shadowing -----------------------------------------------------------

def test_shadowing_detected_when_tracks_stay_close_for_long_enough():
    track_a = [make_detection(t, 51.5, -0.1) for t in range(0, 90, 5)]
    track_b = [make_detection(t, 51.5001, -0.1001) for t in range(0, 90, 5)]  # ~15m away throughout
    assert detect_shadowing(track_a, track_b, max_distance_m=30.0, min_duration_s=60.0) is True


def test_no_shadowing_when_tracks_are_far_apart():
    track_a = [make_detection(t, 51.5, -0.1) for t in range(0, 90, 5)]
    track_b = [make_detection(t, 51.6, -0.2) for t in range(0, 90, 5)]  # far away
    assert detect_shadowing(track_a, track_b, max_distance_m=30.0, min_duration_s=60.0) is False


def test_no_shadowing_for_a_brief_close_pass():
    # Close for only ~10s in the middle of an otherwise-distant timeline.
    track_a = [make_detection(t, 51.5 + t * 0.001, -0.1) for t in range(0, 90, 5)]
    track_b = [make_detection(t, 51.5 + t * 0.001, -0.1) for t in range(40, 50, 5)]
    assert detect_shadowing(track_a, track_b, max_distance_m=30.0, min_duration_s=60.0) is False


def test_no_shadowing_with_insufficient_positioned_detections():
    assert detect_shadowing([make_detection(0, 51.5, -0.1)], [make_detection(0, 51.5, -0.1)]) is False


def test_shadowing_picks_the_longest_of_several_close_runs_separated_by_gaps():
    # A short close run (15s, alone below the 60s threshold), a gap wider
    # than max_time_gap_s that must reset the run, then a second close run
    # long enough on its own (70s) to cross the threshold -- proves the
    # function tracks the longest *contiguous* run rather than summing
    # every close moment across the whole timeline.
    times = [0, 5, 10, 15, *range(100, 171, 5)]
    track_a = [make_detection(t, 51.5, -0.1) for t in times]
    track_b = [make_detection(t, 51.5001, -0.1001) for t in times]  # ~15m away throughout
    assert detect_shadowing(track_a, track_b, max_distance_m=30.0, min_duration_s=60.0, max_time_gap_s=10.0) is True


# --- Formations ------------------------------------------------------------

def test_two_tracks_moving_together_form_a_group():
    tracks = [
        make_track(1, 51.5, -0.1, heading_deg=90.0, speed_mps=10.0),
        make_track(2, 51.5001, -0.1001, heading_deg=92.0, speed_mps=10.5),
    ]
    formations = detect_formations(tracks, max_spacing_m=100.0, heading_tolerance_deg=15.0, speed_tolerance_mps=2.0)
    assert len(formations) == 1
    assert set(formations[0]) == {1, 2}


def test_tracks_far_apart_do_not_form_a_group():
    tracks = [
        make_track(1, 51.5, -0.1, heading_deg=90.0, speed_mps=10.0),
        make_track(2, 51.9, -0.5, heading_deg=90.0, speed_mps=10.0),
    ]
    assert detect_formations(tracks, max_spacing_m=100.0) == []


def test_tracks_close_but_on_different_headings_do_not_form_a_group():
    tracks = [
        make_track(1, 51.5, -0.1, heading_deg=0.0, speed_mps=10.0),
        make_track(2, 51.5001, -0.1001, heading_deg=180.0, speed_mps=10.0),
    ]
    assert detect_formations(tracks, max_spacing_m=100.0, heading_tolerance_deg=15.0) == []


def test_tracks_close_but_different_speeds_do_not_form_a_group():
    tracks = [
        make_track(1, 51.5, -0.1, heading_deg=90.0, speed_mps=5.0),
        make_track(2, 51.5001, -0.1001, heading_deg=90.0, speed_mps=25.0),
    ]
    assert detect_formations(tracks, max_spacing_m=100.0, speed_tolerance_mps=2.0) == []


def test_stationary_tracks_never_form_a_formation():
    tracks = [
        make_track(1, 51.5, -0.1, heading_deg=90.0, speed_mps=0.1),
        make_track(2, 51.5001, -0.1001, heading_deg=90.0, speed_mps=0.1),
    ]
    assert detect_formations(tracks, max_spacing_m=100.0, min_speed_mps=1.0) == []


def test_transitive_chain_groups_into_one_formation():
    # A-B close, B-C close, but A-C are not directly within range -- still
    # one coordinated formation via the B link.
    tracks = [
        make_track(1, 51.5000, -0.1000, heading_deg=90.0, speed_mps=10.0),
        make_track(2, 51.5008, -0.1000, heading_deg=90.0, speed_mps=10.0),
        make_track(3, 51.5016, -0.1000, heading_deg=90.0, speed_mps=10.0),
    ]
    formations = detect_formations(tracks, max_spacing_m=100.0)
    assert len(formations) == 1
    assert set(formations[0]) == {1, 2, 3}


def test_a_lone_track_is_not_a_formation():
    tracks = [make_track(1, 51.5, -0.1, heading_deg=90.0, speed_mps=10.0)]
    assert detect_formations(tracks) == []


def test_tracks_missing_heading_or_speed_are_excluded():
    tracks = [
        make_track(1, 51.5, -0.1, heading_deg=None, speed_mps=10.0),
        make_track(2, 51.5001, -0.1001, heading_deg=90.0, speed_mps=None),
    ]
    assert detect_formations(tracks) == []
