from datetime import datetime, timedelta

from app.db import create_detection
from app.models import Detection, SensorType, SensorStatus
from app.sensors import get_sensor_health

NOW = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(sensor_id: str, timestamp: datetime, sensor_type=SensorType.RADAR) -> Detection:
    return Detection(sensor_id=sensor_id, sensor_type=sensor_type, timestamp=timestamp, confidence=0.9)


def test_no_sensors_when_no_detections():
    assert get_sensor_health(now=NOW) == []


def test_sensor_online_within_window():
    create_detection(make_detection("radar-1", NOW - timedelta(seconds=10)))
    health = get_sensor_health(now=NOW)
    assert len(health) == 1
    assert health[0].sensor_id == "radar-1"
    assert health[0].status == SensorStatus.ONLINE


def test_sensor_stale_between_windows():
    create_detection(make_detection("radar-1", NOW - timedelta(seconds=120)))
    health = get_sensor_health(now=NOW)
    assert health[0].status == SensorStatus.STALE


def test_sensor_offline_beyond_stale_window():
    create_detection(make_detection("radar-1", NOW - timedelta(seconds=600)))
    health = get_sensor_health(now=NOW)
    assert health[0].status == SensorStatus.OFFLINE


def test_multiple_sensors_report_independently_and_correct_type():
    create_detection(make_detection("radar-1", NOW - timedelta(seconds=5), SensorType.RADAR))
    create_detection(make_detection("cam-1", NOW - timedelta(seconds=5), SensorType.CAMERA))
    health = {h.sensor_id: h for h in get_sensor_health(now=NOW)}
    assert set(health) == {"radar-1", "cam-1"}
    assert health["radar-1"].sensor_type == SensorType.RADAR
    assert health["cam-1"].sensor_type == SensorType.CAMERA


def test_only_most_recent_detection_determines_status():
    # An old detection followed by a fresh one should read as online, not
    # stale/offline from the earlier one -- exercises the "latest row per
    # sensor" query (app/sensors.py's portable correlated-subquery form).
    create_detection(make_detection("radar-1", NOW - timedelta(seconds=600)))
    create_detection(make_detection("radar-1", NOW - timedelta(seconds=5)))
    health = get_sensor_health(now=NOW)
    assert len(health) == 1
    assert health[0].status == SensorStatus.ONLINE
    assert health[0].last_seen == NOW - timedelta(seconds=5)
