from app.adapters.dji_droneid import build_detection_payload, is_valid_packet

# Shape matches RUB-SysSec/DroneSecurity's own documented example output
# (fetched from its README) -- see app/adapters/dji_droneid.py for the
# full provenance/caveats.
VALID_PACKET = {
    "serial_number": "1ZNDH1234A0001",
    "longitude": 7.267960786785307,
    "latitude": 51.446866781640146,
    "altitude": 39.32,
    "app_lat": 43.26826445428658,
    "app_lon": 6.640125363111847,
    "longitude_home": 7.26794359805882,
    "latitude_home": 51.446883970366635,
    "crc-packet": "0xABCD",
    "crc-calculated": "0xABCD",
}


def test_valid_packet_produces_a_detection_at_the_drones_own_position():
    payload = build_detection_payload(VALID_PACKET, sensor_id="dji-rf-1")
    assert payload is not None
    assert payload["latitude"] == VALID_PACKET["latitude"]
    assert payload["longitude"] == VALID_PACKET["longitude"]
    assert payload["altitude_m"] == VALID_PACKET["altitude"]
    assert payload["sensor_type"] == "rf"


def test_operator_and_home_position_are_carried_in_raw_data_not_top_level():
    payload = build_detection_payload(VALID_PACKET, sensor_id="dji-rf-1")
    assert payload["raw_data"]["operator_latitude"] == VALID_PACKET["app_lat"]
    assert payload["raw_data"]["operator_longitude"] == VALID_PACKET["app_lon"]
    assert payload["raw_data"]["home_latitude"] == VALID_PACKET["latitude_home"]
    assert payload["raw_data"]["home_longitude"] == VALID_PACKET["longitude_home"]
    assert payload["raw_data"]["serial_number"] == VALID_PACKET["serial_number"]
    # The drone's own position is what the tracker actually tracks -- must
    # not also be duplicated as a top-level "operator position" detection.
    assert "operator_latitude" not in payload
    assert "app_lat" not in payload


def test_crc_mismatch_is_rejected():
    packet = {**VALID_PACKET, "crc-packet": "0xABCD", "crc-calculated": "0x0000"}
    assert build_detection_payload(packet, sensor_id="dji-rf-1") is None
    assert is_valid_packet(packet) is False


def test_matching_crc_is_accepted():
    assert is_valid_packet(VALID_PACKET) is True


def test_missing_crc_fields_does_not_block_a_packet():
    # A different tool version, or a test fixture, might not report CRC
    # status at all -- that alone shouldn't disqualify an otherwise
    # complete packet.
    packet = {k: v for k, v in VALID_PACKET.items() if not k.startswith("crc")}
    assert is_valid_packet(packet) is True
    assert build_detection_payload(packet, sensor_id="dji-rf-1") is not None


def test_missing_position_returns_none():
    packet = {k: v for k, v in VALID_PACKET.items() if k not in ("latitude", "longitude")}
    assert build_detection_payload(packet, sensor_id="dji-rf-1") is None


def test_missing_altitude_is_tolerated():
    packet = {k: v for k, v in VALID_PACKET.items() if k != "altitude"}
    payload = build_detection_payload(packet, sensor_id="dji-rf-1")
    assert payload is not None
    assert payload["altitude_m"] is None


def test_confidence_is_configurable():
    payload = build_detection_payload(VALID_PACKET, sensor_id="dji-rf-1", confidence=0.5)
    assert payload["confidence"] == 0.5
