from datetime import datetime, timedelta

from app.db import (
    create_detection,
    create_incident,
    create_track,
    create_zone,
    get_detection,
    get_zone_by_name,
    list_incidents,
    list_tracks,
    update_incident,
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


def test_get_detection_round_trips_all_fields():
    detection = create_detection(
        Detection(
            sensor_id="radar-1",
            sensor_type=SensorType.RADAR,
            timestamp=BASE_TIME,
            latitude=51.5,
            longitude=-0.1,
            altitude_m=100.0,
            azimuth_deg=45.0,
            range_m=500.0,
            confidence=0.85,
            raw_data={"foo": "bar"},
        )
    )
    fetched = get_detection(detection.id)
    assert fetched.sensor_id == "radar-1"
    assert fetched.latitude == 51.5
    assert fetched.azimuth_deg == 45.0
    assert fetched.range_m == 500.0
    assert fetched.raw_data == {"foo": "bar"}


def test_get_detection_missing_id_returns_none():
    assert get_detection(999999) is None


def test_get_zone_by_name_finds_and_misses():
    create_zone(Zone(name="alpha", zone_type=ZoneType.RESTRICTED, polygon=[(0, 0), (0, 1), (1, 1)]))
    assert get_zone_by_name("alpha") is not None
    assert get_zone_by_name("does-not-exist") is None


def test_update_incident_persists_status_and_closed_at():
    track = create_track(
        Track(
            track_uid="t1", first_seen=BASE_TIME, last_seen=BASE_TIME,
            status=TrackStatus.ACTIVE, classification=Classification.DRONE,
        )
    )
    zone = create_zone(Zone(name="z1", zone_type=ZoneType.RESTRICTED, polygon=[(0, 0), (0, 1), (1, 1)]))
    incident = create_incident(
        Incident(
            incident_uid="i1", incident_type=IncidentType.ZONE_INCURSION,
            severity=IncidentSeverity.HIGH, status=IncidentStatus.OPEN,
            track_id=track.id, zone_id=zone.id, opened_at=BASE_TIME,
        )
    )
    incident.status = IncidentStatus.RESOLVED
    incident.closed_at = BASE_TIME + timedelta(minutes=5)
    incident.acknowledged_by = "admin"
    updated = update_incident(incident)

    reloaded = [i for i in list_incidents() if i.id == updated.id][0]
    assert reloaded.status == IncidentStatus.RESOLVED
    assert reloaded.closed_at == BASE_TIME + timedelta(minutes=5)
    assert reloaded.acknowledged_by == "admin"


def test_list_tracks_pagination():
    for i in range(5):
        create_track(
            Track(
                track_uid=f"t{i}", first_seen=BASE_TIME, last_seen=BASE_TIME + timedelta(seconds=i),
                status=TrackStatus.ACTIVE, classification=Classification.UNKNOWN,
            )
        )
    page_one = list_tracks(limit=2, offset=0)
    page_two = list_tracks(limit=2, offset=2)
    assert len(page_one) == 2
    assert len(page_two) == 2
    assert {t.id for t in page_one} != {t.id for t in page_two}
    assert len(list_tracks()) == 5  # unbounded default


def test_list_incidents_pagination_and_status_filter():
    track = create_track(
        Track(
            track_uid="t1", first_seen=BASE_TIME, last_seen=BASE_TIME,
            status=TrackStatus.ACTIVE, classification=Classification.DRONE,
        )
    )
    zone = create_zone(Zone(name="z1", zone_type=ZoneType.RESTRICTED, polygon=[(0, 0), (0, 1), (1, 1)]))
    for i in range(3):
        create_incident(
            Incident(
                incident_uid=f"i{i}", incident_type=IncidentType.ZONE_INCURSION,
                severity=IncidentSeverity.HIGH, status=IncidentStatus.OPEN,
                track_id=track.id, zone_id=zone.id, opened_at=BASE_TIME + timedelta(seconds=i),
            )
        )
    assert len(list_incidents(limit=2)) == 2
    assert len(list_incidents(status="open")) == 3
    assert len(list_incidents(status="resolved")) == 0
