import uuid
from datetime import datetime

from app.db import create_detection, create_incident, create_track, create_zone, list_incidents, update_incident
from app.incidents import (
    check_predicted_incursions,
    check_zone_incident_resolutions,
    check_zone_incidents,
    close_incidents_for_closed_track,
)
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
    Zone,
    ZoneType,
)
from app.util import utcnow

SQUARE = [(51.0, -0.1), (51.0, 0.1), (51.2, 0.1), (51.2, -0.1)]


def make_track(site_id: int, **overrides) -> Track:
    defaults = {
        "site_id": site_id,
        "track_uid": "track-1",
        "first_seen": datetime(2026, 1, 1, 12, 0, 0),
        "last_seen": datetime(2026, 1, 1, 12, 0, 0),
        "status": TrackStatus.ACTIVE,
        "classification": Classification.DRONE,
        "latitude": 51.1,
        "longitude": 0.0,
        "altitude_m": 100,
    }
    defaults.update(overrides)
    # Incidents carry a foreign key to track, so it must actually exist in
    # the DB rather than just being an in-memory Track instance.
    return create_track(Track(**defaults))


def test_incident_opened_for_track_in_restricted_zone(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    incidents = check_zone_incidents(make_track(site_id))
    assert len(incidents) == 1
    assert incidents[0].zone_id is not None
    assert list_incidents(site_id=site_id) == incidents


def test_no_incident_for_track_outside_zone(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    assert check_zone_incidents(make_track(site_id, latitude=60.0, longitude=60.0)) == []


def test_no_incident_for_non_restricted_zone(site_id):
    create_zone(Zone(site_id=site_id, name="monitor-zone", zone_type=ZoneType.MONITORING, polygon=SQUARE))
    assert check_zone_incidents(make_track(site_id)) == []


def test_duplicate_incursion_does_not_open_second_incident(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id)
    first = check_zone_incidents(track)
    second = check_zone_incidents(track)
    assert len(first) == 1
    assert second == []
    assert len(list_incidents(site_id=site_id)) == 1


def test_severity_matches_classification(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    incidents = check_zone_incidents(make_track(site_id, classification=Classification.DRONE))
    assert incidents[0].severity == IncidentSeverity.HIGH


def _seed_detection(site_id: int, track_id: int, sensor_type: SensorType) -> None:
    create_detection(
        Detection(
            site_id=site_id, sensor_id=f"{sensor_type.value}-1", sensor_type=sensor_type,
            track_id=track_id, timestamp=datetime(2026, 1, 1, 12, 0, 0),
            latitude=51.1, longitude=0.0, confidence=0.9,
        )
    )


def test_severity_escalates_when_corroborated_by_multiple_sensor_types(site_id):
    # A DRONE reading independently confirmed by two distinct sensor
    # types is more actionable than the identical label from a single
    # sensor -- severity used to be keyed only on the classification
    # label and couldn't tell the two apart.
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id, classification=Classification.DRONE)
    _seed_detection(site_id, track.id, SensorType.RADAR)
    _seed_detection(site_id, track.id, SensorType.RF)

    incidents = check_zone_incidents(track)
    assert incidents[0].severity == IncidentSeverity.CRITICAL  # HIGH escalated one level
    assert "corroborated by 2 sensor types" in incidents[0].description


def test_severity_does_not_escalate_from_a_single_sensor_type(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id, classification=Classification.DRONE)
    _seed_detection(site_id, track.id, SensorType.RADAR)
    _seed_detection(site_id, track.id, SensorType.RADAR)  # same type again, still just one

    incidents = check_zone_incidents(track)
    assert incidents[0].severity == IncidentSeverity.HIGH  # unescalated
    assert "corroborated" not in incidents[0].description


def test_severity_escalation_caps_at_critical(site_id):
    # AIRCRAFT/BIRD both map to LOW; FRIENDLY maps to MEDIUM. Confirm the
    # escalation table doesn't error or overshoot for the one classification
    # already at the ceiling once escalated.
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id, classification=Classification.DRONE)
    _seed_detection(site_id, track.id, SensorType.RADAR)
    _seed_detection(site_id, track.id, SensorType.CAMERA)
    _seed_detection(site_id, track.id, SensorType.ACOUSTIC)

    incidents = check_zone_incidents(track)
    assert incidents[0].severity == IncidentSeverity.CRITICAL


def test_friendly_classification_is_not_downgraded_to_low_severity(site_id):
    # Regression test: FRIENDLY comes from an unauthenticated, unsigned
    # raw_data.operator_id claim (see app/allowlist.py), not independent
    # sensor evidence like AIRCRAFT/BIRD -- it must not fully suppress
    # incident severity to LOW on an attacker's say-so.
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    incidents = check_zone_incidents(make_track(site_id, classification=Classification.FRIENDLY))
    assert incidents[0].severity == IncidentSeverity.MEDIUM


def test_incident_respects_zone_altitude_band(site_id):
    create_zone(
        Zone(
            site_id=site_id,
            name="banded-rz",
            zone_type=ZoneType.RESTRICTED,
            polygon=SQUARE,
            min_altitude_m=200,
            max_altitude_m=400,
        )
    )
    below_band = check_zone_incidents(make_track(site_id, altitude_m=50))
    in_band = check_zone_incidents(make_track(site_id, track_uid="track-2", altitude_m=300))
    assert below_band == []
    assert len(in_band) == 1


def test_predicted_incursion_opens_incident_for_projected_zone_entry(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    # Just south of the zone, heading due north at 50 m/s: well inside the
    # zone within the default 30s prediction horizon.
    track = make_track(site_id, latitude=50.99, longitude=0.0, heading_deg=0.0, speed_mps=50.0)
    assert check_zone_incidents(track) == []  # not inside the zone yet
    predicted = check_predicted_incursions(track)
    assert len(predicted) == 1
    assert predicted[0].incident_type == IncidentType.PREDICTED_INCURSION


def test_predicted_incursion_not_raised_for_track_already_inside_zone(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id, latitude=51.1, longitude=0.0, heading_deg=0.0, speed_mps=50.0)
    assert check_predicted_incursions(track) == []


def test_no_predicted_incursion_for_near_stationary_track(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id, latitude=50.99, longitude=0.0, heading_deg=0.0, speed_mps=0.1)
    assert check_predicted_incursions(track) == []


def test_after_action_report_endpoint_returns_full_incident_story(site_id):
    from fastapi.testclient import TestClient

    from app.db import create_detection
    from app.main import app
    from app.models import Detection, SensorType

    with TestClient(app) as client:
        create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
        track = make_track(site_id)
        create_detection(
            Detection(
                site_id=site_id, sensor_id="radar-1", sensor_type=SensorType.RADAR,
                timestamp=track.first_seen, track_id=track.id, latitude=51.1, longitude=0.0, confidence=0.9,
            )
        )
        incidents = check_zone_incidents(track)
        incident_id = incidents[0].id

        r = client.get(f"/api/incidents/{incident_id}/report")
        assert r.status_code == 200
        body = r.json()
        assert body["incident"]["incident_type"] == "zone_incursion"
        assert body["track"]["classification"] == "drone"
        assert body["zone"]["name"] == "rz"
        assert body["detection_count"] == 1
        assert body["sensors_involved"] == ["radar-1"]


def test_after_action_report_404s_for_unknown_incident(site_id):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/incidents/999999/report")
        assert r.status_code == 404


def test_zone_incident_auto_closes_when_track_exits_the_zone(site_id):
    # Regression test: check_zone_incidents() only ever opened an
    # incident on entry -- nothing closed it back out once the track's
    # position left again, so it sat open/acknowledged forever even for
    # a track that flew straight through the zone in seconds.
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id)
    incidents = check_zone_incidents(track)
    assert len(incidents) == 1

    # Still inside the zone -- nothing to close yet.
    assert check_zone_incident_resolutions(track) == []

    track.latitude, track.longitude = 60.0, 60.0  # well outside the zone
    closed = check_zone_incident_resolutions(track)
    assert len(closed) == 1
    assert closed[0].id == incidents[0].id
    assert closed[0].status == IncidentStatus.RESOLVED
    assert closed[0].closed_at is not None
    assert "auto-closed" in closed[0].description
    # Never fabricates that an operator reviewed it.
    assert closed[0].acknowledged_by is None


def test_zone_incident_auto_close_preserves_an_existing_acknowledgment(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id)
    incident = check_zone_incidents(track)[0]
    incident.status = IncidentStatus.ACKNOWLEDGED
    incident.acknowledged_by = "operator-1"
    update_incident(incident)

    track.latitude, track.longitude = 60.0, 60.0
    closed = check_zone_incident_resolutions(track)
    assert len(closed) == 1
    assert closed[0].acknowledged_by == "operator-1"


def test_close_incidents_for_closed_track_closes_every_open_incident(site_id):
    create_zone(Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=SQUARE))
    track = make_track(site_id)
    zone_incident = check_zone_incidents(track)[0]
    # A behavioral incident (loitering) isn't zone-based, so
    # check_zone_incident_resolutions never touches it -- only a track
    # actually closing should close this one.
    loitering_incident = create_incident(
        Incident(
            site_id=site_id, incident_uid=str(uuid.uuid4()), incident_type=IncidentType.LOITERING,
            severity=IncidentSeverity.MEDIUM, status=IncidentStatus.OPEN, track_id=track.id,
            opened_at=utcnow(), description="loitering",
        )
    )

    closed = close_incidents_for_closed_track(track)
    closed_ids = {i.id for i in closed}
    assert closed_ids == {zone_incident.id, loitering_incident.id}
    assert all(i.status == IncidentStatus.RESOLVED for i in closed)


def test_close_incidents_for_closed_track_is_a_noop_with_none_open(site_id):
    track = make_track(site_id)
    assert close_incidents_for_closed_track(track) == []
