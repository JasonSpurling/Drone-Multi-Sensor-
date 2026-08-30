"""app.sensors.get_sensor_health -- health status derived from detections,
plus a MISSING entry for a registered sensor that has never reported one
at all. Regression coverage for a real gap: before this, a registered-
but-silent sensor was simply absent from the result, indistinguishable
from a sensor nobody ever configured.
"""

from datetime import timedelta

from app.db import upsert_sensor_registration
from app.models import Detection, SensorStatus
from app.sensors import get_sensor_health
from app.tracking import associate_detection
from app.util import utcnow

BASE_TIME = utcnow()


def _post_detection(site_id: int, sensor_id: str, timestamp, sensor_type: str = "radar") -> None:
    associate_detection(
        Detection(
            site_id=site_id, sensor_id=sensor_id, sensor_type=sensor_type,
            timestamp=timestamp, latitude=51.5, longitude=-0.1, confidence=0.9,
        )
    )


def _register(site_id: int, sensor_id: str, *, active: bool = True, sensor_type: str = "radar") -> None:
    upsert_sensor_registration(
        sensor_id=sensor_id, site_id=site_id, sensor_type=sensor_type,
        latitude=51.5, longitude=-0.1, altitude_m=None, azimuth_reference_deg=0.0, active=active,
    )


def test_empty_when_nothing_reported_or_registered(site_id):
    assert get_sensor_health(site_id) == []


def test_recently_reporting_sensor_is_online(site_id):
    _post_detection(site_id, "radar-1", BASE_TIME)
    health = get_sensor_health(site_id, now=BASE_TIME + timedelta(seconds=10))
    assert len(health) == 1
    assert health[0].status == SensorStatus.ONLINE
    assert health[0].last_seen == BASE_TIME


def test_registered_but_never_reporting_sensor_is_missing(site_id):
    # The regression case: a sensor with a known mounting position (has
    # been registered) but zero detections ever -- must show up as a
    # distinct, actionable "missing" status, not be silently absent.
    _register(site_id, "radar-2")
    health = get_sensor_health(site_id)
    assert len(health) == 1
    assert health[0].sensor_id == "radar-2"
    assert health[0].status == SensorStatus.MISSING
    assert health[0].last_seen is None


def test_a_registered_sensor_that_has_reported_shows_real_health_not_missing(site_id):
    # Registration must never override real, derived health -- a sensor
    # that actually reported is ONLINE/STALE/OFFLINE like any other, even
    # though it's also registered.
    _register(site_id, "radar-3")
    _post_detection(site_id, "radar-3", BASE_TIME)
    health = get_sensor_health(site_id, now=BASE_TIME + timedelta(seconds=10))
    assert len(health) == 1
    assert health[0].status == SensorStatus.ONLINE
    assert health[0].last_seen == BASE_TIME


def test_a_deactivated_registration_with_no_detections_is_not_flagged(site_id):
    # A deliberately decommissioned sensor isn't a gap to surface.
    _register(site_id, "radar-4", active=False)
    assert get_sensor_health(site_id) == []


def test_results_are_sorted_by_sensor_id(site_id):
    _register(site_id, "z-sensor")
    _post_detection(site_id, "a-sensor", BASE_TIME)
    health = get_sensor_health(site_id, now=BASE_TIME + timedelta(seconds=10))
    assert [h.sensor_id for h in health] == ["a-sensor", "z-sensor"]
