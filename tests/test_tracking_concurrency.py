"""Regression test for the read-then-write race in associate_detection:
concurrent detections for the same object, arriving on different threads
(as FastAPI's threadpool would dispatch them), must not spawn duplicate
tracks.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app.db import list_tracks
from app.models import Detection, SensorType
from app.tracking import associate_detection

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def test_concurrent_detections_for_same_object_join_one_track(site_id):
    def post(i: int):
        return associate_detection(
            Detection(
                site_id=site_id,
                sensor_id=f"sensor-{i}",
                sensor_type=SensorType.RADAR,
                timestamp=BASE_TIME,
                latitude=51.5,
                longitude=-0.1,
                altitude_m=100,
                confidence=0.9,
            )
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(post, range(16)))

    track_ids = {d.track_id for d in results}
    assert len(track_ids) == 1  # all detections landed on the same track
    assert len(list_tracks(site_id=site_id)) == 1
