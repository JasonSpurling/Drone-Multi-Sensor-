from datetime import datetime, timedelta

from app.db import create_detection, list_detections, purge_old_detections
from app.models import Detection, SensorType


def make_detection(site_id: int, timestamp: datetime) -> Detection:
    return Detection(
        site_id=site_id, sensor_id="radar-1", sensor_type=SensorType.RADAR, timestamp=timestamp, confidence=0.9
    )


def test_purge_removes_only_detections_older_than_cutoff(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    create_detection(make_detection(site_id, now - timedelta(days=40)))
    create_detection(make_detection(site_id, now - timedelta(days=20)))
    create_detection(make_detection(site_id, now - timedelta(days=1)))

    removed = purge_old_detections(before=now - timedelta(days=30))

    assert removed == 1
    remaining = list_detections(site_id=site_id)
    assert len(remaining) == 2
    assert all(d.timestamp >= now - timedelta(days=30) for d in remaining)


def test_purge_returns_zero_when_nothing_is_old_enough(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    create_detection(make_detection(site_id, now))
    assert purge_old_detections(before=now - timedelta(days=30)) == 0
