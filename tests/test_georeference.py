from app.db import upsert_sensor_registration
from app.georeference import georeference
from app.models import Detection, SensorType


def make_detection(**overrides) -> Detection:
    defaults = {"sensor_id": "radar-1", "sensor_type": SensorType.RADAR, "confidence": 0.9}
    defaults.update(overrides)
    return Detection(**defaults)


def test_passthrough_when_latlon_already_present():
    detection = make_detection(latitude=51.5, longitude=-0.1)
    result = georeference(detection)
    assert result.latitude == 51.5
    assert result.longitude == -0.1


def test_passthrough_when_no_azimuth_or_range():
    detection = make_detection()
    result = georeference(detection)
    assert result.latitude is None


def test_passthrough_when_sensor_not_registered(site_id):
    detection = make_detection(site_id=site_id, azimuth_deg=90.0, range_m=1000.0)
    result = georeference(detection)
    assert result.latitude is None


def test_computes_latlon_from_registered_sensor_position(site_id):
    upsert_sensor_registration(
        sensor_id="radar-1",
        site_id=site_id,
        sensor_type="radar",
        latitude=51.5,
        longitude=-0.1,
        altitude_m=50.0,
        azimuth_reference_deg=0.0,
    )
    detection = make_detection(site_id=site_id, azimuth_deg=90.0, range_m=1000.0)
    result = georeference(detection)
    assert result.latitude is not None and result.longitude is not None
    # Bearing 90 (due east) from the sensor: latitude barely changes, longitude increases.
    assert abs(result.latitude - 51.5) < 0.001
    assert result.longitude > -0.1
    assert result.altitude_m == 50.0  # filled in from the sensor's registered altitude
    # Marks the position as computed, not sensor-reported -- see
    # app/remote_id.py's canonical_message, which relies on this to bind a
    # signature to what the sensor actually signed rather than this
    # estimate.
    assert result.georeferenced is True


def test_passthrough_detections_are_not_marked_georeferenced():
    detection = make_detection(latitude=51.5, longitude=-0.1)
    result = georeference(detection)
    assert result.georeferenced is False


def test_azimuth_reference_offset_is_applied(site_id):
    # Sensor mounted facing 45 deg off true north: azimuth_deg=0 (sensor's
    # "straight ahead") should resolve to compass bearing 45, not 0.
    upsert_sensor_registration(
        sensor_id="radar-2",
        site_id=site_id,
        sensor_type="radar",
        latitude=51.5,
        longitude=-0.1,
        altitude_m=None,
        azimuth_reference_deg=45.0,
    )
    detection = make_detection(site_id=site_id, sensor_id="radar-2", azimuth_deg=0.0, range_m=1000.0)
    result = georeference(detection)
    # Bearing 45 moves both lat and lon in the positive direction.
    assert result.latitude > 51.5
    assert result.longitude > -0.1


def test_existing_altitude_is_not_overwritten_by_sensor_altitude(site_id):
    upsert_sensor_registration(
        sensor_id="radar-3",
        site_id=site_id,
        sensor_type="radar",
        latitude=51.5,
        longitude=-0.1,
        altitude_m=999.0,
        azimuth_reference_deg=0.0,
    )
    detection = make_detection(
        site_id=site_id, sensor_id="radar-3", azimuth_deg=0.0, range_m=100.0, altitude_m=42.0
    )
    result = georeference(detection)
    assert result.altitude_m == 42.0
