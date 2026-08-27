from datetime import datetime, timedelta

from app.db import KalmanStateRecord, create_track, list_tracks, upsert_kalman_state
from app.models import Classification, Detection, SensorType, Track, TrackStatus
from app.tracking import associate_detection, associate_detections_batch

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(site_id: int, **overrides) -> Detection:
    defaults = {
        "site_id": site_id,
        "sensor_id": "radar-1", "sensor_type": SensorType.RADAR, "timestamp": BASE_TIME, "confidence": 0.9
    }
    defaults.update(overrides)
    return Detection(**defaults)


def _seed_track(site_id: int, track_uid: str, latitude: float, longitude: float, at: datetime) -> int:
    """Create a track directly (bypassing gating) with a tight, confident
    IMM state at a known position -- used to set up two distinct nearby
    tracks without the seeding detections merging into one.
    """
    track = create_track(
        Track(
            site_id=site_id,
            track_uid=track_uid, first_seen=at, last_seen=at, status=TrackStatus.ACTIVE,
            classification=Classification.UNKNOWN, latitude=latitude, longitude=longitude,
        )
    )
    upsert_kalman_state(
        KalmanStateRecord(
            track_id=track.id,
            ref_lat=latitude,
            ref_lon=longitude,
            models=[
                {
                    "x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0,
                    "covariance": [[25.0, 0, 0, 0], [0, 25.0, 0, 0], [0, 0, 25.0, 0], [0, 0, 0, 25.0]],
                },
                {
                    "x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0,
                    "covariance": [[25.0, 0, 0, 0], [0, 25.0, 0, 0], [0, 0, 25.0, 0], [0, 0, 0, 25.0]],
                },
            ],
            mode_probabilities=[0.9, 0.1],
            updated_at=at,
        )
    )
    return track.id


def _seed_two_close_tracks(site_id: int) -> tuple[int, int]:
    """Two tracks ~15m apart, both with tight position confidence -- close
    enough that a detection near either one gates against both, but
    confident enough that gating doesn't degenerate into "gates against
    everything" from sheer uncertainty.
    """
    track_a_id = _seed_track(site_id, "track-a", 51.50000, 0.00000, BASE_TIME)
    track_b_id = _seed_track(site_id, "track-b", 51.50000, 0.00017, BASE_TIME)  # ~11.8m east
    return track_a_id, track_b_id


def test_batch_assignment_never_puts_two_detections_on_one_track(site_id):
    track_a_id, track_b_id = _seed_two_close_tracks(site_id)
    t = BASE_TIME + timedelta(seconds=10)

    # Two near-simultaneous detections, both gated to both tracks, each
    # slightly closer to the "other" track's original position than to
    # their own recent update -- exactly the ambiguous case a per-
    # detection greedy resolution can double-assign.
    d1 = make_detection(site_id, timestamp=t, latitude=51.50000, longitude=0.00002)
    d2 = make_detection(site_id, timestamp=t, latitude=51.50000, longitude=0.00015)

    results = associate_detections_batch([d1, d2])

    assert len(results) == 2
    assigned_track_ids = {r.track_id for r in results}
    assert len(assigned_track_ids) == 2, "both detections landed on the same track in one batch"
    # No new tracks were spawned -- both detections matched an existing track.
    assert assigned_track_ids == {track_a_id, track_b_id}


def test_sequential_single_calls_can_pile_both_detections_onto_one_track(site_id):
    # Regression/contrast test: proves the failure mode associate_detections_batch
    # exists to prevent. Sequential single-detection calls have no memory
    # of "already used this scan", so both of two simultaneous detections
    # near two close tracks CAN legally attach to the same track,
    # silently starving the other one of an update this scan.
    track_a_id, track_b_id = _seed_two_close_tracks(site_id)
    t = BASE_TIME + timedelta(seconds=10)

    d1 = make_detection(site_id, timestamp=t, latitude=51.50000, longitude=0.00002)
    d2 = make_detection(
        site_id, timestamp=t, latitude=51.50000, longitude=0.00004
    )  # also close to A after d1 updates it

    r1 = associate_detection(d1)
    r2 = associate_detection(d2)

    # Both detections land on track A; track B gets no update at all this
    # scan. This is the concrete failure mode associate_detections_batch
    # exists to prevent -- documenting that it's real, not asserting it's
    # desired behavior.
    assert r1.track_id == track_a_id
    assert r2.track_id == track_a_id
    assert r1.track_id == r2.track_id != track_b_id


def test_batch_gates_and_spawns_new_tracks_same_as_single_detection(site_id):
    far_detection = make_detection(site_id, latitude=10.0, longitude=10.0)
    results = associate_detections_batch([far_detection])
    assert len(results) == 1
    assert results[0].track_id is not None
    assert len(list_tracks(site_id=site_id)) == 1


def test_batch_with_multiple_new_tracks_spawns_one_each(site_id):
    detections = [
        make_detection(site_id, sensor_id="s1", latitude=10.0, longitude=10.0),
        make_detection(site_id, sensor_id="s2", latitude=-10.0, longitude=-10.0),
    ]
    results = associate_detections_batch(detections)
    assert len({r.track_id for r in results}) == 2
    assert len(list_tracks(site_id=site_id)) == 2


def test_empty_batch_returns_empty_list():
    assert associate_detections_batch([]) == []


def test_batch_respects_time_gate(site_id):
    track_id, _ = _seed_two_close_tracks(site_id)
    stale = make_detection(
        site_id, timestamp=BASE_TIME + timedelta(seconds=100), latitude=51.50000, longitude=0.00000
    )
    results = associate_detections_batch([stale])
    assert results[0].track_id != track_id  # too old to gate -> new track


def test_batch_detection_with_no_position_always_spawns_new_track(site_id):
    _seed_two_close_tracks(site_id)
    no_position = make_detection(site_id, latitude=None, longitude=None)
    results = associate_detections_batch([no_position])
    assert results[0].track_id is not None
    assert len(list_tracks(site_id=site_id)) == 3  # the 2 seeded tracks + this new one
