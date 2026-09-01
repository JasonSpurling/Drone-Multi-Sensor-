from app.adapters.astm_remote_id import (
    ASD_STAN_WIFI_VENDOR_OUI,
    DIRECT_REMOTE_ID_APPLICATION_CODE,
    build_detection_payload,
    merge_fields,
    parse_wifi_vendor_ie,
)


def test_merge_accumulates_across_multiple_messages():
    state = {}
    state = merge_fields(state, {"uas_id": "1581F582N9K2K12345", "ua_type": 2})
    state = merge_fields(state, {"latitude": 51.5, "longitude": -0.1, "height_m": 50.0})
    state = merge_fields(state, {"operator_id": "OP12345678"})
    assert state == {
        "uas_id": "1581F582N9K2K12345", "ua_type": 2,
        "latitude": 51.5, "longitude": -0.1, "height_m": 50.0,
        "operator_id": "OP12345678",
    }


def test_merge_does_not_erase_earlier_fields_when_a_later_message_omits_them():
    state = merge_fields({}, {"operator_id": "OP12345678"})
    state = merge_fields(state, {"latitude": 51.5, "longitude": -0.1})
    assert state["operator_id"] == "OP12345678"
    assert state["latitude"] == 51.5


def test_merge_ignores_none_values_rather_than_overwriting():
    state = merge_fields({}, {"operator_id": "OP12345678"})
    state = merge_fields(state, {"operator_id": None, "latitude": 51.5, "longitude": -0.1})
    assert state["operator_id"] == "OP12345678"


def test_no_detection_without_a_location_message_yet():
    state = merge_fields({}, {"uas_id": "1581F582N9K2K12345", "operator_id": "OP12345678"})
    assert build_detection_payload(state, sensor_id="remote-id-1") is None


def test_detection_emitted_once_location_is_known():
    state = merge_fields({}, {"uas_id": "1581F582N9K2K12345"})
    state = merge_fields(state, {"latitude": 51.5, "longitude": -0.1, "height_m": 50.0})
    payload = build_detection_payload(state, sensor_id="remote-id-1")
    assert payload is not None
    assert payload["latitude"] == 51.5
    assert payload["longitude"] == -0.1
    assert payload["altitude_m"] == 50.0
    assert payload["sensor_type"] == "rf"
    assert payload["raw_data"]["uas_id"] == "1581F582N9K2K12345"


def test_operator_position_is_context_not_the_tracked_position():
    state = merge_fields({}, {"latitude": 51.5, "longitude": -0.1})
    state = merge_fields(state, {"operator_latitude": 51.501, "operator_longitude": -0.099})
    payload = build_detection_payload(state, sensor_id="remote-id-1")
    assert payload["latitude"] == 51.5  # the drone's position, not the operator's
    assert payload["raw_data"]["operator_latitude"] == 51.501
    assert payload["raw_data"]["operator_longitude"] == -0.099


def test_confidence_is_configurable():
    state = merge_fields({}, {"latitude": 51.5, "longitude": -0.1})
    payload = build_detection_payload(state, sensor_id="remote-id-1", confidence=0.5)
    assert payload["confidence"] == 0.5


def test_no_authorized_operator_field_that_would_imply_trust():
    # This must never look like the signed Remote ID path (app/remote_id.py) --
    # no "signature" field, since a raw broadcast has no cryptographic proof.
    state = merge_fields({}, {"latitude": 51.5, "longitude": -0.1, "operator_id": "OP12345678"})
    payload = build_detection_payload(state, sensor_id="remote-id-1")
    assert "signature" not in payload["raw_data"]


def _wifi_vendor_ie(oui: bytes, app_code: int, counter: int, message_pack: bytes) -> bytes:
    return oui + bytes([app_code]) + bytes([counter]) + message_pack


def test_parse_wifi_vendor_ie_extracts_the_message_pack_from_a_real_odid_ie():
    message_pack = b"\x0d" + b"\xaa" * 25  # one ODID message header byte + a fake 25-byte message
    info = _wifi_vendor_ie(
        ASD_STAN_WIFI_VENDOR_OUI, DIRECT_REMOTE_ID_APPLICATION_CODE, counter=7, message_pack=message_pack
    )
    assert parse_wifi_vendor_ie(info) == message_pack


def test_parse_wifi_vendor_ie_rejects_a_different_vendors_oui():
    # WiFi beacons routinely carry other vendors' vendor-specific IEs
    # (WPS, QoS extensions, ...) -- this must not mistake one for Open
    # Drone ID just because it happens to be IE 0xDD too.
    info = _wifi_vendor_ie(b"\x00\x50\xf2", DIRECT_REMOTE_ID_APPLICATION_CODE, counter=0, message_pack=b"\x00" * 26)
    assert parse_wifi_vendor_ie(info) is None


def test_parse_wifi_vendor_ie_rejects_the_right_oui_with_a_different_application_code():
    info = _wifi_vendor_ie(ASD_STAN_WIFI_VENDOR_OUI, app_code=0xFF, counter=0, message_pack=b"\x00" * 26)
    assert parse_wifi_vendor_ie(info) is None


def test_parse_wifi_vendor_ie_rejects_too_short_a_payload():
    # Fewer than 5 bytes can't even contain oui(3) + app_code(1) + counter(1).
    assert parse_wifi_vendor_ie(ASD_STAN_WIFI_VENDOR_OUI + b"\x0d") is None


def test_parse_wifi_vendor_ie_handles_an_empty_message_pack():
    info = _wifi_vendor_ie(ASD_STAN_WIFI_VENDOR_OUI, DIRECT_REMOTE_ID_APPLICATION_CODE, counter=0, message_pack=b"")
    assert parse_wifi_vendor_ie(info) == b""
