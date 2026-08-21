from datetime import datetime, timedelta

from app.db import create_detection, create_track, list_incidents
from app.incidents import check_formation_incidents, check_loitering_incident, check_shadowing_incidents
from app.models import Classification, Detection, IncidentType, SensorType, Track, TrackStatus

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


def test_no_shadowing_incident_for_distant_tracks(site_id):
    track_a = make_track(site_id, "far-a")
    track_b = make_track(site_id, "far-b")
    seed_history(site_id, track_a.id, [(t, 51.5, -0.1) for t in range(0, 90, 5)])
    seed_history(site_id, track_b.id, [(t, 52.5, -0.5) for t in range(0, 90, 5)])
    assert check_shadowing_incidents([track_a, track_b]) == []
