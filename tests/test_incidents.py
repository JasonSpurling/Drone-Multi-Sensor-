from datetime import datetime

from app.db import create_track, create_zone, list_incidents
from app.incidents import check_predicted_incursions, check_zone_incidents
from app.models import (
    Classification,
    IncidentSeverity,
    IncidentType,
    Track,
    TrackStatus,
    Zone,
    ZoneType,
)

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
