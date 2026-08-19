from app.adapters.mavlink import build_detection_payload


def test_decodes_position_from_e7_fields():
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=1, lat_e7=515000000, lon_e7=-1000000,
        alt_mm=100000, heading_cdeg=9000, vx_cms=0, vy_cms=0,
    )
    assert payload is not None
    assert payload["latitude"] == 51.5
    assert payload["longitude"] == -0.1
    assert payload["altitude_m"] == 100.0


def test_zero_zero_position_means_no_gps_fix_and_is_skipped():
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=1, lat_e7=0, lon_e7=0,
        alt_mm=0, heading_cdeg=0, vx_cms=0, vy_cms=0,
    )
    assert payload is None


def test_heading_sentinel_reports_as_none():
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=1, lat_e7=515000000, lon_e7=-1000000,
        alt_mm=100000, heading_cdeg=65535, vx_cms=0, vy_cms=0,
    )
    assert payload["raw_data"]["heading_deg"] is None


def test_heading_converts_from_centidegrees():
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=1, lat_e7=515000000, lon_e7=-1000000,
        alt_mm=100000, heading_cdeg=9000, vx_cms=0, vy_cms=0,
    )
    assert payload["raw_data"]["heading_deg"] == 90.0


def test_ground_speed_from_velocity_components():
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=1, lat_e7=515000000, lon_e7=-1000000,
        alt_mm=100000, heading_cdeg=9000, vx_cms=300, vy_cms=400,
    )
    assert payload["raw_data"]["ground_speed_mps"] == 5.0  # 3-4-5 triangle, cm/s -> m/s


def test_sensor_type_is_other_not_friendly_or_adsb():
    # Unauthenticated telemetry -- must not imply any trust classification.
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=1, lat_e7=515000000, lon_e7=-1000000,
        alt_mm=100000, heading_cdeg=9000, vx_cms=0, vy_cms=0,
    )
    assert payload["sensor_type"] == "other"
    assert "operator_id" not in payload["raw_data"]
    assert "signature" not in payload["raw_data"]


def test_sysid_is_carried_in_raw_data():
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=42, lat_e7=515000000, lon_e7=-1000000,
        alt_mm=100000, heading_cdeg=9000, vx_cms=0, vy_cms=0,
    )
    assert payload["raw_data"]["mavlink_sysid"] == 42


def test_confidence_is_configurable():
    payload = build_detection_payload(
        sensor_id="mavlink-1", sysid=1, lat_e7=515000000, lon_e7=-1000000,
        alt_mm=100000, heading_cdeg=9000, vx_cms=0, vy_cms=0, confidence=0.5,
    )
    assert payload["confidence"] == 0.5
