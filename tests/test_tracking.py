import math
import uuid
from datetime import datetime, timedelta

import pytest

from app.db import create_incident, create_track, get_incident, list_tracks
from app.models import (
    Classification,
    Detection,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    SensorType,
    Track,
    TrackStatus,
)
from app.tracking import (
    _find_matching_track,
    _spawn_track,
    associate_detection,
    expire_stale_tracks,
    haversine_distance_m,
)

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(site_id: int, **overrides) -> Detection:
    defaults = {
        "site_id": site_id,
        "sensor_id": "radar-1",
        "sensor_type": SensorType.RADAR,
        "timestamp": BASE_TIME,
        "latitude": 51.5,
        "longitude": -0.1,
        "altitude_m": 100,
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return Detection(**defaults)


def test_haversine_zero_for_identical_points():
    assert haversine_distance_m(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0)


def test_haversine_known_distance():
    # Roughly 1 degree of latitude ~= 111.32 km.
    distance = haversine_distance_m(51.0, 0.0, 52.0, 0.0)
    assert distance == pytest.approx(111_320, rel=0.01)


def test_first_detection_creates_new_track(site_id):
    detection = associate_detection(make_detection(site_id))
    tracks = list_tracks(site_id=site_id)
    assert len(tracks) == 1
    assert detection.track_id == tracks[0].id
    assert tracks[0].status == TrackStatus.ACTIVE


def test_nearby_detection_joins_existing_track(site_id):
    first = associate_detection(make_detection(site_id))
    second = associate_detection(
        make_detection(
            site_id,
            timestamp=BASE_TIME + timedelta(seconds=5),
            latitude=51.5001,
            longitude=-0.1001,
        )
    )
    assert second.track_id == first.track_id
    assert len(list_tracks(site_id=site_id)) == 1


def test_far_detection_creates_separate_track(site_id):
    first = associate_detection(make_detection(site_id))
    second = associate_detection(
        make_detection(
            site_id,
            timestamp=BASE_TIME + timedelta(seconds=5),
            latitude=52.5,
            longitude=1.5,
        )
    )
    assert second.track_id != first.track_id
    assert len(list_tracks(site_id=site_id)) == 2


def test_detection_within_the_coarse_gate_but_outside_the_mahalanobis_gate_creates_a_separate_track(site_id):
    # ~300m offset clears the coarse pre-filter (500m default) but, with a
    # tight measurement uncertainty from a high-confidence detection, is
    # still statistically far enough (squared Mahalanobis distance) to
    # fail the IMM gate -- must fall through to spawning a new track
    # rather than silently accepting a bad match just because it passed
    # the coarse distance check.
    first = associate_detection(make_detection(site_id, confidence=0.99))
    second = associate_detection(
        make_detection(
            site_id, confidence=0.99, timestamp=BASE_TIME + timedelta(milliseconds=100), latitude=51.5044,
        )
    )
    assert second.track_id != first.track_id
    assert len(list_tracks(site_id=site_id)) == 2


def test_a_track_with_no_resolved_position_is_never_matched_against(site_id):
    # An RF/acoustic sensor's azimuth-only detection can spawn a track
    # with no lat/lon at all (see _spawn_track) -- such a track can never
    # be re-matched (there's no position to gate against), so a
    # subsequent normal, positioned detection must spawn its own track
    # rather than erroring or silently attaching to the position-less one.
    first = associate_detection(
        make_detection(site_id, sensor_type=SensorType.RF, latitude=None, longitude=None, azimuth_deg=90.0)
    )
    second = associate_detection(make_detection(site_id, timestamp=BASE_TIME + timedelta(seconds=1)))
    assert second.track_id != first.track_id
    assert len(list_tracks(site_id=site_id)) == 2


def test_detection_outside_time_gate_creates_separate_track(site_id):
    first = associate_detection(make_detection(site_id))
    second = associate_detection(
        make_detection(site_id, timestamp=BASE_TIME + timedelta(seconds=60))
    )
    assert second.track_id != first.track_id
    assert len(list_tracks(site_id=site_id)) == 2


def test_a_track_still_active_but_outside_its_own_time_gate_is_not_matched(monkeypatch, site_id):
    # Default config has TRACK_STALE_SECONDS == TRACK_TIME_GATE_SECONDS
    # (both 30s), so any detection late enough to fail the time gate has
    # already had its target track expired to LOST by expire_stale_tracks
    # -- excluded from the active-track query before the time-gate check
    # in _find_matching_track ever runs. Widening the stale window here
    # isolates that inner check: the track is still ACTIVE (and so still
    # considered) but the gap is long enough to fail its own time gate.
    monkeypatch.setattr("app.tracking.TRACK_TIME_GATE_SECONDS", 10.0)
    monkeypatch.setattr("app.tracking.TRACK_STALE_SECONDS", 120.0)

    first = associate_detection(make_detection(site_id))
    second = associate_detection(make_detection(site_id, timestamp=BASE_TIME + timedelta(seconds=50)))

    assert second.track_id != first.track_id
    tracks = list_tracks(site_id=site_id)
    assert len(tracks) == 2
    assert all(t.status == TrackStatus.ACTIVE for t in tracks)  # neither was expired


def test_associate_detection_requires_a_site_id(site_id):
    detection = make_detection(site_id)
    detection.site_id = None
    with pytest.raises(ValueError, match=r"requires detection\.site_id"):
        associate_detection(detection)


def test_track_upgrades_from_unknown_to_drone(site_id):
    associate_detection(make_detection(site_id, sensor_type=SensorType.RF, confidence=0.5))  # -> UNKNOWN
    associate_detection(
        make_detection(
            site_id,
            timestamp=BASE_TIME + timedelta(seconds=5),
            sensor_type=SensorType.RF,
            confidence=0.95,  # -> DRONE
        )
    )
    track = list_tracks(site_id=site_id)[0]
    assert track.classification == Classification.DRONE


def test_track_upgrades_from_bird_to_drone(site_id):
    # Regression test: a track first read as a low-confidence camera/acoustic
    # return (classified BIRD) must still be promoted to DRONE once later
    # detections clearly show a drone -- it must not get stuck at BIRD.
    associate_detection(make_detection(site_id, sensor_type=SensorType.CAMERA, confidence=0.1))  # -> BIRD
    associate_detection(
        make_detection(
            site_id,
            timestamp=BASE_TIME + timedelta(seconds=5),
            sensor_type=SensorType.CAMERA,
            confidence=0.95,  # -> DRONE
        )
    )
    track = list_tracks(site_id=site_id)[0]
    assert track.classification == Classification.DRONE


def test_track_gains_velocity_and_heading_from_repeated_detections(site_id):
    # Move ~10 m/s due east (heading 90) between fixes, spaced 5s apart.
    lon_step = 0.00006  # ~5m east near this latitude, x2 per 5s tick below
    for i in range(6):
        associate_detection(
            make_detection(
                site_id,
                timestamp=BASE_TIME + timedelta(seconds=5 * i),
                latitude=51.5,
                longitude=-0.1 + lon_step * i,
            )
        )
    track = list_tracks(site_id=site_id)[0]
    assert track.speed_mps is not None and track.speed_mps > 0.5
    assert track.heading_deg is not None
    assert 45 < track.heading_deg < 135  # roughly eastward


def test_track_position_uncertainty_shrinks_with_more_detections(site_id):
    associate_detection(make_detection(site_id))
    first_track = list_tracks(site_id=site_id)[0]
    first_uncertainty = first_track.position_uncertainty_m

    for i in range(1, 5):
        associate_detection(
            make_detection(
                site_id,
                timestamp=BASE_TIME + timedelta(seconds=5 * i),
                latitude=51.5 + 0.00001 * i,
                longitude=-0.1,
            )
        )
    later_track = list_tracks(site_id=site_id)[0]
    assert later_track.position_uncertainty_m < first_uncertainty


def test_fast_mover_still_gates_onto_predicted_position(site_id):
    # A steadily-moving object several TRACK_DISTANCE_GATE_M past its last
    # raw fix should still join the same track, because gating is against
    # the Kalman-predicted position (which moves with it), not the stale
    # last-known position.
    associate_detection(make_detection(site_id, latitude=51.5, longitude=-0.10000))
    associate_detection(
        make_detection(site_id, timestamp=BASE_TIME + timedelta(seconds=5), latitude=51.5, longitude=-0.09850)
    )
    third = associate_detection(
        make_detection(site_id, timestamp=BASE_TIME + timedelta(seconds=10), latitude=51.5, longitude=-0.09700)
    )
    assert len(list_tracks(site_id=site_id)) == 1
    assert third.track_id == list_tracks(site_id=site_id)[0].id


def test_fast_mover_with_a_multi_second_gap_still_gates_when_raw_distance_exceeds_the_static_gate(site_id):
    # Regression test: the coarse pre-Mahalanobis prefilter used to compare
    # against the track's raw last-known lat/lon with a fixed
    # TRACK_DISTANCE_GATE_M (500m default), so a fast mover that goes quiet
    # for a few seconds could end up further than 500m from its stale last
    # fix even though the IMM-predicted position (using its known
    # velocity) is right where the next detection lands -- rejected before
    # the Mahalanobis/IMM math (which does account for this) ever ran,
    # spawning a spurious duplicate track. ~150 m/s over a 4s gap covers
    # ~600m, past the static 500m gate but well within what a
    # velocity-aware gate should still accept.
    lat, lon = 51.5, -0.3
    heading_deg, speed_mps = 90.0, 150.0

    def _step(lat, lon, dt_s):
        distance_m = speed_mps * dt_s
        d_lat = (distance_m * math.cos(math.radians(heading_deg))) / 111_320.0
        meters_per_degree_lon = 111_320.0 * math.cos(math.radians(lat))
        d_lon = (distance_m * math.sin(math.radians(heading_deg))) / meters_per_degree_lon
        return lat + d_lat, lon + d_lon

    # Two close-together fixes to establish a real velocity estimate.
    first = associate_detection(make_detection(site_id, sensor_id="radar-1", latitude=lat, longitude=lon))
    lat, lon = _step(lat, lon, 1.0)
    second = associate_detection(
        make_detection(
            site_id, sensor_id="radar-1", timestamp=BASE_TIME + timedelta(seconds=1), latitude=lat, longitude=lon
        )
    )
    assert second.track_id == first.track_id

    # Then a 4s gap -- ~600m of travel, past the static 500m gate.
    lat, lon = _step(lat, lon, 4.0)
    third = associate_detection(
        make_detection(
            site_id, sensor_id="radar-1", timestamp=BASE_TIME + timedelta(seconds=5), latitude=lat, longitude=lon
        )
    )
    assert third.track_id == first.track_id
    assert len(list_tracks(site_id=site_id)) == 1


def test_fast_aircraft_at_one_hertz_stays_on_one_track(site_id):
    # Regression test: a ~210 m/s aircraft (typical ADS-B ground speed)
    # reporting once per second must stay associated to a single track.
    # With too-tight an initial velocity prior, the filter's first predict
    # step assumes near-zero velocity, so the object's real next position
    # (~200m away after 1s) falls outside the gate before the filter has
    # had a second update to learn its actual velocity -- exactly the
    # warm-up failure this test guards against.
    lat, lon = 51.5, -0.3
    heading_deg, speed_mps = 200.0, 210.0
    track_ids = []
    for i in range(8):
        detection = associate_detection(
            make_detection(
                site_id,
                sensor_id="adsb-1",
                sensor_type=SensorType.ADSB,
                timestamp=BASE_TIME + timedelta(seconds=i),
                latitude=lat,
                longitude=lon,
                confidence=0.98,
            )
        )
        track_ids.append(detection.track_id)
        distance_m = speed_mps * 1.0
        d_lat = (distance_m * math.cos(math.radians(heading_deg))) / 111_320.0
        meters_per_degree_lon = 111_320.0 * math.cos(math.radians(lat))
        d_lon = (distance_m * math.sin(math.radians(heading_deg))) / meters_per_degree_lon
        lat += d_lat
        lon += d_lon

    assert len(set(track_ids)) == 1
    assert len(list_tracks(site_id=site_id)) == 1


def test_confident_classification_does_not_decay_back_to_bird(site_id):
    associate_detection(make_detection(site_id, sensor_type=SensorType.CAMERA, confidence=0.95))  # -> DRONE
    associate_detection(
        make_detection(
            site_id,
            timestamp=BASE_TIME + timedelta(seconds=5),
            sensor_type=SensorType.CAMERA,
            confidence=0.1,  # would be BIRD on its own
        )
    )
    track = list_tracks(site_id=site_id)[0]
    assert track.classification == Classification.DRONE


def test_classification_confidence_falls_as_contradicting_evidence_accumulates_without_downgrading(site_id):
    # The label stays DRONE (the upgrade-only ratchet, see the test just
    # above) even as more recent evidence increasingly looks like BIRD --
    # but confidence in that stored DRONE label should honestly fall,
    # rather than staying pinned at whatever it was on the first detection.
    associate_detection(make_detection(site_id, sensor_type=SensorType.CAMERA, confidence=0.95))  # -> DRONE
    first_confidence = list_tracks(site_id=site_id)[0].classification_confidence
    assert first_confidence == 1.0

    for i in range(5):
        associate_detection(
            make_detection(
                site_id, timestamp=BASE_TIME + timedelta(seconds=5 * (i + 1)),
                sensor_type=SensorType.CAMERA, confidence=0.1,  # BIRD-ish on its own
            )
        )
    track = list_tracks(site_id=site_id)[0]
    assert track.classification == Classification.DRONE  # never downgraded
    assert track.classification_confidence < first_confidence  # but less confident in it now


def test_aircraft_category_is_carried_from_detection_to_track(site_id):
    associate_detection(
        make_detection(
            site_id, sensor_type=SensorType.ADSB, confidence=0.99,
            raw_data={"hex_ident": "4ca593", "category": "A7"},
        )
    )
    track = list_tracks(site_id=site_id)[0]
    assert track.aircraft_category == "A7"


def test_aircraft_category_defaults_to_none_when_never_reported(site_id):
    associate_detection(make_detection(site_id, sensor_type=SensorType.RADAR))
    track = list_tracks(site_id=site_id)[0]
    assert track.aircraft_category is None


def test_aircraft_category_updates_to_the_latest_report(site_id):
    associate_detection(
        make_detection(
            site_id, sensor_type=SensorType.ADSB, confidence=0.99,
            raw_data={"hex_ident": "4ca593", "category": "A3"},
        )
    )
    associate_detection(
        make_detection(
            site_id, sensor_type=SensorType.ADSB, confidence=0.99,
            timestamp=BASE_TIME + timedelta(seconds=5),
            raw_data={"hex_ident": "4ca593", "category": "A7"},
        )
    )
    track = list_tracks(site_id=site_id)[0]
    assert track.aircraft_category == "A7"


def test_track_closing_auto_closes_its_still_open_incidents(site_id):
    # Regression test: a track that simply flew out of sensor range (no
    # more detections, so app.incidents.check_zone_incident_resolutions'
    # position-based check never runs again for it) previously left every
    # incident it opened stuck open/acknowledged forever -- the track
    # itself going stale -> lost -> closed had nothing wired to it at all.
    old_time = BASE_TIME - timedelta(seconds=10_000)
    track = create_track(
        Track(
            site_id=site_id, track_uid="stale-track", first_seen=old_time, last_seen=old_time,
            status=TrackStatus.LOST, classification=Classification.DRONE, latitude=51.5, longitude=-0.1,
        )
    )
    incident = create_incident(
        Incident(
            site_id=site_id, incident_uid=str(uuid.uuid4()), incident_type=IncidentType.ZONE_INCURSION,
            severity=IncidentSeverity.HIGH, status=IncidentStatus.OPEN, track_id=track.id,
            opened_at=old_time, description="entered restricted zone",
        )
    )

    expire_stale_tracks(site_id, now=BASE_TIME)

    assert list_tracks(site_id=site_id, status=TrackStatus.CLOSED.value)[0].id == track.id
    closed_incident = get_incident(incident.id, site_id)
    assert closed_incident.status == IncidentStatus.RESOLVED
    assert closed_incident.closed_at is not None
    assert "auto-closed" in closed_incident.description


def test_aircraft_category_is_not_cleared_by_a_later_detection_without_one(site_id):
    associate_detection(
        make_detection(
            site_id, sensor_type=SensorType.ADSB, confidence=0.99,
            raw_data={"hex_ident": "4ca593", "category": "A7"},
        )
    )
    associate_detection(
        make_detection(
            site_id, sensor_type=SensorType.ADSB, confidence=0.99,
            timestamp=BASE_TIME + timedelta(seconds=5),
            raw_data={"hex_ident": "4ca593"},
        )
    )
    track = list_tracks(site_id=site_id)[0]
    assert track.aircraft_category == "A7"


def test_find_matching_track_returns_none_without_a_resolved_position():
    detection = make_detection(1, latitude=None, longitude=None)
    assert _find_matching_track(detection, measurement_variance=100.0) is None


def test_find_matching_track_returns_none_without_a_site_id():
    detection = make_detection(1)
    detection.site_id = None
    assert _find_matching_track(detection, measurement_variance=100.0) is None


def test_find_matching_track_skips_a_track_with_no_kalman_state(site_id):
    # A track can exist (ACTIVE, positioned) with no Kalman state row only
    # in a hand-crafted/direct-DB scenario -- every real path that gives a
    # track a position also saves a filter for it in the same step (see
    # _spawn_track). Still must be handled defensively rather than
    # crashing on get_kalman_state's None result.
    from app.db import create_track

    create_track(
        Track(
            site_id=site_id, track_uid="no-filter", first_seen=BASE_TIME, last_seen=BASE_TIME,
            status=TrackStatus.ACTIVE, classification=Classification.UNKNOWN,
            latitude=51.5, longitude=-0.1,
        )
    )
    detection = make_detection(site_id, timestamp=BASE_TIME + timedelta(seconds=1))
    assert _find_matching_track(detection, measurement_variance=100.0) is None


def test_spawn_track_requires_a_site_id():
    detection = make_detection(1)
    detection.site_id = None
    with pytest.raises(ValueError, match=r"requires detection\.site_id"):
        _spawn_track(detection, measurement_variance=100.0)
