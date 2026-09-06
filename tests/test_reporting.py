from datetime import datetime, timedelta

from app.models import Detection, Incident, IncidentSeverity, IncidentStatus, IncidentType, Track
from app.reporting import build_incident_report

START = datetime(2026, 1, 1)
END = datetime(2026, 1, 8)


def make_incident(
    incident_type=IncidentType.ZONE_INCURSION, severity=IncidentSeverity.MEDIUM,
    status=IncidentStatus.OPEN, opened_at=None, closed_at=None, acknowledged_by=None,
) -> Incident:
    return Incident(
        incident_uid="uid", incident_type=incident_type, severity=severity, status=status,
        track_id=1, zone_id=1, opened_at=opened_at or START, closed_at=closed_at,
        acknowledged_by=acknowledged_by,
    )


def test_empty_report_has_zero_counts():
    report = build_incident_report([], START, END)
    assert report["total_incidents"] == 0
    assert report["by_type"] == {}
    assert report["resolution_time_seconds"]["mean"] is None


def test_total_incidents_counted():
    incidents = [make_incident(), make_incident(), make_incident()]
    report = build_incident_report(incidents, START, END)
    assert report["total_incidents"] == 3


def test_breakdown_by_type():
    incidents = [
        make_incident(incident_type=IncidentType.ZONE_INCURSION),
        make_incident(incident_type=IncidentType.ZONE_INCURSION),
        make_incident(incident_type=IncidentType.LOITERING),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["by_type"] == {"zone_incursion": 2, "loitering": 1}


def test_breakdown_by_severity_and_status():
    incidents = [
        make_incident(severity=IncidentSeverity.HIGH, status=IncidentStatus.OPEN),
        make_incident(severity=IncidentSeverity.LOW, status=IncidentStatus.RESOLVED),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["by_severity"] == {"high": 1, "low": 1}
    assert report["by_status"] == {"open": 1, "resolved": 1}


def test_resolution_time_only_counts_closed_incidents():
    incidents = [
        make_incident(opened_at=START, closed_at=START + timedelta(hours=1)),
        make_incident(opened_at=START, closed_at=None),  # still open, not counted
    ]
    report = build_incident_report(incidents, START, END)
    assert report["resolution_time_seconds"]["count"] == 1
    assert report["resolution_time_seconds"]["mean"] == 3600.0
    assert report["resolution_time_seconds"]["median"] == 3600.0
    assert report["resolution_time_seconds"]["max"] == 3600.0


def test_unacknowledged_count():
    incidents = [
        make_incident(acknowledged_by=None),
        make_incident(acknowledged_by="operator-1"),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["unacknowledged_count"] == 1


def test_daily_counts_grouped_by_date():
    incidents = [
        make_incident(opened_at=datetime(2026, 1, 1, 9)),
        make_incident(opened_at=datetime(2026, 1, 1, 15)),
        make_incident(opened_at=datetime(2026, 1, 2, 9)),
    ]
    report = build_incident_report(incidents, START, END)
    assert report["daily_counts"] == {"2026-01-01": 2, "2026-01-02": 1}


def test_range_is_echoed_in_report():
    report = build_incident_report([], START, END)
    assert report["range"]["start"] == START.isoformat()
    assert report["range"]["end"] == END.isoformat()


def test_after_action_report_includes_incident_details():
    from app.reporting import build_after_action_report

    incident = make_incident(
        status=IncidentStatus.RESOLVED,
        opened_at=START,
        closed_at=START + timedelta(minutes=5),
        acknowledged_by="operator-1",
    )
    report = build_after_action_report(incident, track=None, zone=None, detections=[])
    assert report["incident"]["incident_uid"] == "uid"
    assert report["incident"]["status"] == "resolved"
    assert report["incident"]["response_time_seconds"] == 300.0
    assert report["incident"]["acknowledged_by"] == "operator-1"


def test_after_action_report_response_time_is_none_while_open():
    from app.reporting import build_after_action_report

    incident = make_incident(status=IncidentStatus.OPEN, closed_at=None)
    report = build_after_action_report(incident, track=None, zone=None, detections=[])
    assert report["incident"]["response_time_seconds"] is None


def test_after_action_report_omits_track_and_zone_when_unresolvable():
    from app.reporting import build_after_action_report

    incident = make_incident()
    report = build_after_action_report(incident, track=None, zone=None, detections=[])
    assert report["track"] is None
    assert report["zone"] is None
    assert report["detections"] == []
    assert report["sensors_involved"] == []


def test_after_action_report_includes_track_and_zone_summary():
    from app.models import Classification, TrackStatus, Zone, ZoneType
    from app.reporting import build_after_action_report

    incident = make_incident()
    track = Track(
        track_uid="t-1", first_seen=START, last_seen=START + timedelta(minutes=2),
        status=TrackStatus.ACTIVE, classification=Classification.DRONE,
        latitude=51.1, longitude=0.0, altitude_m=120.0, speed_mps=12.0, heading_deg=90.0,
    )
    zone = Zone(name="Central RZ", zone_type=ZoneType.RESTRICTED, polygon=[(0, 0), (0, 1), (1, 0)])
    report = build_after_action_report(incident, track=track, zone=zone, detections=[])
    assert report["track"]["classification"] == "drone"
    assert report["track"]["final_position"] == {"latitude": 51.1, "longitude": 0.0, "altitude_m": 120.0}
    assert report["zone"] == {"name": "Central RZ", "zone_type": "restricted"}


def test_after_action_report_lists_detections_and_sensors_involved():
    from app.models import SensorType
    from app.reporting import build_after_action_report

    incident = make_incident()
    detections = [
        Detection(
            sensor_id="radar-1", sensor_type=SensorType.RADAR, timestamp=START,
            latitude=51.0, longitude=0.0, confidence=0.9,
        ),
        Detection(
            sensor_id="camera-1", sensor_type=SensorType.CAMERA, timestamp=START + timedelta(seconds=5),
            latitude=51.0, longitude=0.0, confidence=0.4,
        ),
    ]
    report = build_after_action_report(incident, track=None, zone=None, detections=detections)
    assert report["detection_count"] == 2
    assert report["sensors_involved"] == ["camera-1", "radar-1"]
    assert report["detections"][0]["sensor_id"] == "radar-1"
    assert report["detections"][0]["confidence"] == 0.9


def test_after_action_report_extracts_real_identity_fragments_from_raw_data():
    """_extract_identification surfaces real per-aircraft identity fields
    a sensor already decoded (DJI DroneID serial_number, ASTERIX radar's
    Mode S address/callsign/squawk) -- not a manufacturer/model guess.
    """
    from app.models import SensorType
    from app.reporting import build_after_action_report

    incident = make_incident()
    detections = [
        Detection(
            sensor_id="radar-1", sensor_type=SensorType.RADAR, timestamp=START,
            latitude=51.0, longitude=0.0, confidence=0.9,
            raw_data={"aircraft_address": "ABC123", "callsign": "SPEEDBIRD1"},
        ),
        Detection(
            sensor_id="dji-rid-1", sensor_type=SensorType.RF, timestamp=START + timedelta(seconds=5),
            latitude=51.0, longitude=0.0, confidence=0.8,
            raw_data={"serial_number": "0W9DH1A0010SNL"},
        ),
    ]
    report = build_after_action_report(incident, track=None, zone=None, detections=detections)
    assert report["identification"] == {
        "aircraft_address": "ABC123", "callsign": "SPEEDBIRD1", "serial_number": "0W9DH1A0010SNL",
    }


def test_after_action_report_identification_is_empty_without_any_raw_data():
    from app.reporting import build_after_action_report

    incident = make_incident()
    report = build_after_action_report(incident, track=None, zone=None, detections=[])
    assert report["identification"] == {}


def test_after_action_report_includes_aircraft_category_when_known():
    from app.models import Classification, TrackStatus
    from app.reporting import build_after_action_report

    incident = make_incident()
    track = Track(
        track_uid="t-1", first_seen=START, last_seen=START, status=TrackStatus.ACTIVE,
        classification=Classification.AIRCRAFT, aircraft_category="A7",
    )
    report = build_after_action_report(incident, track=track, zone=None, detections=[])
    assert report["track"]["aircraft_category"] == "A7"
