"""Direct tests of app.db helpers not otherwise exercised at the DB layer:
the site_id-required guards on the create_*() helpers (every real caller
already has a validated site_id -- app.tracking, app.incidents, etc. --
so these are only reachable by calling db.py directly, the same reasoning
tests/test_consumer.py's/test_tracking.py's own site_id-guard tests
follow), a few thin lookup helpers with no existing direct coverage
(get_detection, get_kalman_state, get_key_usage), and list_incidents'
status-filter branch.
"""

from datetime import datetime

import pytest

from app.db import (
    create_detection,
    create_incident,
    create_track,
    create_zone,
    get_detection,
    get_kalman_state,
    get_key_usage,
    list_incidents,
    update_zone,
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

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def test_create_detection_requires_a_site_id():
    detection = Detection(sensor_id="s1", sensor_type=SensorType.RADAR, confidence=0.9)
    detection.site_id = None
    with pytest.raises(ValueError, match=r"create_detection requires detection\.site_id"):
        create_detection(detection)


def test_create_track_requires_a_site_id():
    track = Track(
        track_uid="t1", first_seen=BASE_TIME, last_seen=BASE_TIME,
        status=TrackStatus.ACTIVE, classification=Classification.UNKNOWN,
    )
    track.site_id = None
    with pytest.raises(ValueError, match=r"create_track requires track\.site_id"):
        create_track(track)


def test_create_incident_requires_a_site_id():
    incident = Incident(
        incident_uid="uid-1", incident_type=IncidentType.ZONE_INCURSION, severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN, opened_at=BASE_TIME,
    )
    incident.site_id = None
    with pytest.raises(ValueError, match=r"create_incident requires incident\.site_id"):
        create_incident(incident)


def test_create_zone_requires_a_site_id():
    zone = Zone(name="rz", zone_type=ZoneType.RESTRICTED, polygon=[(0.0, 0.0), (0.0, 1.0), (1.0, 0.0)])
    zone.site_id = None
    with pytest.raises(ValueError, match=r"create_zone requires zone\.site_id"):
        create_zone(zone)


def test_update_zone_requires_an_id_and_a_site_id(site_id):
    zone = create_zone(
        Zone(site_id=site_id, name="rz", zone_type=ZoneType.RESTRICTED, polygon=[(0.0, 0.0), (0.0, 1.0), (1.0, 0.0)])
    )
    zone.site_id = None
    with pytest.raises(ValueError, match=r"update_zone requires zone\.id and zone\.site_id"):
        update_zone(zone)


def test_get_detection_returns_a_real_row_by_id_or_none_when_missing(site_id):
    created = create_detection(
        Detection(site_id=site_id, sensor_id="s1", sensor_type=SensorType.RADAR, confidence=0.9)
    )
    assert get_detection(created.id, site_id) == created
    assert get_detection(999999, site_id) is None


def test_get_kalman_state_returns_none_for_a_track_with_no_saved_filter(site_id):
    track = create_track(
        Track(
            site_id=site_id, track_uid="t1", first_seen=BASE_TIME, last_seen=BASE_TIME,
            status=TrackStatus.ACTIVE, classification=Classification.UNKNOWN,
        )
    )
    assert get_kalman_state(track.id) is None


def test_get_key_usage_returns_empty_for_an_empty_list_of_hashes():
    assert get_key_usage([]) == {}


def test_list_incidents_filters_by_status(site_id):
    open_incident = create_incident(
        Incident(
            site_id=site_id, incident_uid="uid-open", incident_type=IncidentType.ZONE_INCURSION,
            severity=IncidentSeverity.HIGH, status=IncidentStatus.OPEN, opened_at=BASE_TIME,
        )
    )
    create_incident(
        Incident(
            site_id=site_id, incident_uid="uid-resolved", incident_type=IncidentType.ZONE_INCURSION,
            severity=IncidentSeverity.HIGH, status=IncidentStatus.RESOLVED, opened_at=BASE_TIME,
        )
    )

    results = list_incidents(site_id=site_id, status=IncidentStatus.OPEN.value)

    assert len(results) == 1
    assert results[0].id == open_incident.id
