import pytest

from app.adapters.asterix import build_detection_payload

# Shape matches what asterix4py.AsterixParser(datagram).get_result() actually
# returns for a real CAT048 record (verified against the library's own
# sample/cat048.ast file) -- these tests exercise build_detection_payload
# against that shape directly, without depending on asterix4py itself being
# installed (it's an optional extra, see requirements-radar.txt).
FULL_RECORD = {
    "cat": 48,
    "010": {"SAC": 25, "SIC": 201},
    "140": {"ToD": 27354.6015625},
    "040": {"RHO": 197.68359375, "THETA": 340.13671875},
    "070": {"V": 0, "G": 0, "L": 0, "spare": 0, "Mode3A": "1000"},
    "090": {"V": 0, "G": 0, "FL": 330.0},
    "220": {"ACAddr": "3C660C"},
    "240": {"TId": "DLH65A  "},
}


def test_decodes_position_from_rho_theta():
    payload = build_detection_payload(FULL_RECORD, sensor_id="asterix-1")
    assert payload is not None
    assert payload["azimuth_deg"] == pytest.approx(340.13671875)
    assert payload["range_m"] == pytest.approx(197.68359375 * 1852.0)


def test_decodes_altitude_from_valid_flight_level():
    payload = build_detection_payload(FULL_RECORD, sensor_id="asterix-1")
    assert payload["altitude_m"] == pytest.approx(330.0 * 100 * 0.3048)


def test_invalid_flight_level_is_omitted():
    record = {**FULL_RECORD, "090": {"V": 1, "G": 0, "FL": 330.0}}
    payload = build_detection_payload(record, sensor_id="asterix-1")
    assert payload["altitude_m"] is None


def test_missing_flight_level_item_is_omitted():
    record = {k: v for k, v in FULL_RECORD.items() if k != "090"}
    payload = build_detection_payload(record, sensor_id="asterix-1")
    assert payload["altitude_m"] is None


def test_carries_identity_fields_in_raw_data():
    payload = build_detection_payload(FULL_RECORD, sensor_id="asterix-1")
    assert payload["raw_data"]["sac"] == 25
    assert payload["raw_data"]["sic"] == 201
    assert payload["raw_data"]["aircraft_address"] == "3C660C"
    assert payload["raw_data"]["callsign"] == "DLH65A"  # trailing padding stripped
    assert payload["raw_data"]["mode3a"] == "1000"


def test_record_without_measured_position_returns_none():
    record = {k: v for k, v in FULL_RECORD.items() if k != "040"}
    assert build_detection_payload(record, sensor_id="asterix-1") is None


def test_sensor_type_is_radar_not_adsb():
    payload = build_detection_payload(FULL_RECORD, sensor_id="asterix-1")
    assert payload["sensor_type"] == "radar"


def test_confidence_is_configurable():
    payload = build_detection_payload(FULL_RECORD, sensor_id="asterix-1", confidence=0.6)
    assert payload["confidence"] == 0.6


def test_time_of_day_combines_with_todays_date():
    from datetime import datetime, timedelta, timezone

    payload = build_detection_payload(FULL_RECORD, sensor_id="asterix-1")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    expected = midnight + timedelta(seconds=27354.6015625)
    assert payload["timestamp"] == expected.isoformat()


def test_missing_time_of_day_falls_back_to_now():
    record = {k: v for k, v in FULL_RECORD.items() if k != "140"}
    payload = build_detection_payload(record, sensor_id="asterix-1")
    assert payload["timestamp"] is not None
