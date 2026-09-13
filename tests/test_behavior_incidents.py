from datetime import datetime, timedelta

from app.db import create_detection, create_track, list_incidents
from app.incidents import (
    check_formation_incidents,
    check_loitering_incident,
    check_shadowing_incidents,
    close_incidents_for_closed_track,
)
from app.models import Classification, Detection, IncidentSeverity, IncidentType, SensorType, Track, TrackStatus

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_track(site_id: int, track_uid: str, **overrides) -> Track:
    defaults = {
        "site_id": site_id,
        "track_uid": track_uid, "first_seen": BASE_TIME, "last_seen": BASE_TIME,
        "status": TrackStatus.ACTIVE, "classification": Classification.DRONE,
        "latitude": 51.1, "longitude": 0.0, "altitude_m": 100.0,
    }
    defaults.update(overrides)
    return create_track(Track(**defaults))


def seed_history(site_id: int, track_id: int, points: list[tuple[float, float, float]]) -> None:
    for seconds_offset, lat, lon in points:
        create_detection(
            Detection(
                site_id=site_id, sensor_id="s1", sensor_type=SensorType.RADAR,
                timestamp=BASE_TIME + timedelta(seconds=seconds_offset),
                track_id=track_id, latitude=lat, longitude=lon, confidence=0.9,
            )
        )


def test_loitering_incident_opened_when_track_circles_one_spot(site_id):
    track = make_track(site_id, "loiterer")
    seed_history(site_id, track.id, [(t, 51.1, 0.0) for t in range(0, 130, 10)])

    incident = check_loitering_incident(track)

    assert incident is not None
    assert incident.incident_type == IncidentType.LOITERING
    assert incident.zone_id is None
    assert incident.track_id == track.id


def test_loitering_incident_severity_escalates_when_corroborated(site_id):
    track = make_track(site_id, "loiterer")
    for seconds_offset in range(0, 130, 10):
        create_detection(
            Detection(
                site_id=site_id, sensor_id="s1", sensor_type=SensorType.RADAR,
                timestamp=BASE_TIME + timedelta(seconds=seconds_offset),
                track_id=track.id, latitude=51.1, longitude=0.0, confidence=0.9,
            )
        )
    # A second, distinct sensor type reporting on the same track.
    create_detection(
        Detection(
            site_id=site_id, sensor_id="rf-1", sensor_type=SensorType.RF, timestamp=BASE_TIME,
            track_id=track.id, latitude=51.1, longitude=0.0, confidence=0.9,
        )
    )

    incident = check_loitering_incident(track)

    assert incident is not None
    assert incident.severity == IncidentSeverity.HIGH  # MEDIUM escalated one level
    assert "corroborated by 2 sensor types" in incident.description


def test_no_loitering_incident_for_an_ignored_track(site_id):
    track = make_track(site_id, "loiterer", ignored=True)
    seed_history(site_id, track.id, [(t, 51.1, 0.0) for t in range(0, 130, 10)])
    assert check_loitering_incident(track) is None


def test_no_loitering_incident_for_an_unpersisted_track(site_id):
    track = make_track(site_id, "loiterer")
    track.id = None
    assert check_loitering_incident(track) is None


def test_loitering_incident_not_duplicated_on_repeat_check(site_id):
    track = make_track(site_id, "loiterer")
    seed_history(site_id, track.id, [(t, 51.1, 0.0) for t in range(0, 130, 10)])

    first = check_loitering_incident(track)
    second = check_loitering_incident(track)

    assert first is not None
    assert second is None
    assert len([i for i in list_incidents(site_id=site_id) if i.incident_type == IncidentType.LOITERING]) == 1


def test_no_loitering_incident_for_a_transiting_track(site_id):
    track = make_track(site_id, "transiting")
    seed_history(site_id, track.id, [(t, 51.1 + t * 0.001, 0.0) for t in range(0, 130, 10)])
    assert check_loitering_incident(track) is None


def test_formation_incident_opened_for_tracks_moving_together(site_id):
    track_a = make_track(
        site_id, "formation-a", latitude=51.5, longitude=-0.1, heading_deg=90.0, speed_mps=10.0
    )
    track_b = make_track(
        site_id, "formation-b", latitude=51.5001, longitude=-0.1001, heading_deg=92.0, speed_mps=10.5
    )

    incidents = check_formation_incidents([track_a, track_b])

    assert len(incidents) == 2  # one per track in the formation
    assert {i.track_id for i in incidents} == {track_a.id, track_b.id}
    assert all(i.incident_type == IncidentType.FORMATION for i in incidents)


def test_no_formation_incident_for_unrelated_tracks(site_id):
    track_a = make_track(site_id, "solo-a", latitude=51.5, longitude=-0.1, heading_deg=90.0, speed_mps=10.0)
    track_b = make_track(site_id, "solo-b", latitude=52.5, longitude=-0.5, heading_deg=270.0, speed_mps=5.0)
    assert check_formation_incidents([track_a, track_b]) == []


def test_shadowing_incident_opened_for_two_tracks_staying_close(site_id):
    track_a = make_track(site_id, "shadow-a")
    track_b = make_track(site_id, "shadow-b")
    seed_history(site_id, track_a.id, [(t, 51.5, -0.1) for t in range(0, 90, 5)])
    seed_history(site_id, track_b.id, [(t, 51.5001, -0.1001) for t in range(0, 90, 5)])

    incidents = check_shadowing_incidents([track_a, track_b])

    assert len(incidents) == 2  # one per track in the pair
    assert {i.track_id for i in incidents} == {track_a.id, track_b.id}
    assert all(i.incident_type == IncidentType.SHADOWING for i in incidents)


def test_shadowing_two_different_tracks_at_once_opens_a_separate_incident_for_each_pair(site_id):
    # Regression test: track A shadowing B (already an open incident) must
    # still get its own incident when it also starts shadowing an
    # unrelated track C -- the old dedup key was (track_id, incident_type,
    # site_id) with no reference to *which* other track, so the open A-vs-B
    # incident alone made get_open_behavioral_incident short-circuit and
    # silently drop the A-vs-C relationship entirely.
    # B sits ~22m north of A, C sits ~22m south of A -- each within
    # SHADOWING_MAX_DISTANCE_M (30m default) of A, but ~44m from each
    # other (past the gate), so this is genuinely two separate pairs
    # (A-B, A-C), not one three-way cluster.
    track_a = make_track(site_id, "shadow-a")
    track_b = make_track(site_id, "shadow-b")
    track_c = make_track(site_id, "shadow-c")
    seed_history(site_id, track_a.id, [(t, 51.5, -0.1) for t in range(0, 90, 5)])
    seed_history(site_id, track_b.id, [(t, 51.5002, -0.1) for t in range(0, 90, 5)])

    first_round = check_shadowing_incidents([track_a, track_b])
    assert {i.track_id for i in first_round} == {track_a.id, track_b.id}

    # Now track A also starts shadowing track C (a different pair).
    seed_history(site_id, track_c.id, [(t, 51.4998, -0.1) for t in range(0, 90, 5)])
    second_round = check_shadowing_incidents([track_a, track_b, track_c])

    assert {i.track_id for i in second_round} == {track_a.id, track_c.id}
    all_shadowing = [i for i in list_incidents(site_id=site_id) if i.incident_type == IncidentType.SHADOWING]
    assert len(all_shadowing) == 4  # A-vs-B (x2) and A-vs-C (x2), not deduped against each other
    a_incidents = [i for i in all_shadowing if i.track_id == track_a.id]
    assert {i.related_track_id for i in a_incidents} == {track_b.id, track_c.id}


def test_no_shadowing_incident_for_distant_tracks(site_id):
    track_a = make_track(site_id, "far-a")
    track_b = make_track(site_id, "far-b")
    seed_history(site_id, track_a.id, [(t, 51.5, -0.1) for t in range(0, 90, 5)])
    seed_history(site_id, track_b.id, [(t, 52.5, -0.5) for t in range(0, 90, 5)])
    assert check_shadowing_incidents([track_a, track_b]) == []


def test_close_incidents_for_closed_track_is_a_noop_for_an_unpersisted_track():
    track = Track(
        track_uid="unpersisted", first_seen=BASE_TIME, last_seen=BASE_TIME,
        status=TrackStatus.CLOSED, classification=Classification.DRONE,
    )
    assert close_incidents_for_closed_track(track) == []
