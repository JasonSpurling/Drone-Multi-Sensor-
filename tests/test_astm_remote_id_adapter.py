import sys
import types

from app.adapters.astm_remote_id import (
    ASD_STAN_WIFI_VENDOR_OUI,
    DIRECT_REMOTE_ID_APPLICATION_CODE,
    build_detection_payload,
    extract_message_fields,
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


def _install_fake_dtpyodid(monkeypatch):
    """dtpyodid (requirements-remoteid.txt, an optional extra) isn't
    installed in this environment -- extract_message_fields() does real
    isinstance() checks against its message classes, so a bare mock
    object won't do; these fakes are genuine classes it can instantiate.
    """

    class BasicID:
        def __init__(self, uas_id, ua_type):
            self.uas_id = uas_id
            self.ua_type = ua_type

    class Location:
        def __init__(
            self, latitude, longitude, height, altitude_geo, altitude_baro, speed_horizontal, direction, status
        ):
            self.latitude = latitude
            self.longitude = longitude
            self.height = height
            self.altitude_geo = altitude_geo
            self.altitude_baro = altitude_baro
            self.speed_horizontal = speed_horizontal
            self.direction = direction
            self.status = status

    class OperatorID:
        def __init__(self, operator_id):
            self.operator_id = operator_id

    class SelfID:
        def __init__(self, desc):
            self.desc = desc

    class System:
        def __init__(self, latitude, longitude):
            self.latitude = latitude
            self.longitude = longitude

    basicid_mod = types.ModuleType("dtpyodid.messages.basicid")
    basicid_mod.BasicID = BasicID
    location_mod = types.ModuleType("dtpyodid.messages.location")
    location_mod.Location = Location
    operatorid_mod = types.ModuleType("dtpyodid.messages.operatorid")
    operatorid_mod.OperatorID = OperatorID
    selfid_mod = types.ModuleType("dtpyodid.messages.selfid")
    selfid_mod.SelfID = SelfID
    system_mod = types.ModuleType("dtpyodid.messages.system")
    system_mod.System = System

    monkeypatch.setitem(sys.modules, "dtpyodid", types.ModuleType("dtpyodid"))
    monkeypatch.setitem(sys.modules, "dtpyodid.messages", types.ModuleType("dtpyodid.messages"))
    monkeypatch.setitem(sys.modules, "dtpyodid.messages.basicid", basicid_mod)
    monkeypatch.setitem(sys.modules, "dtpyodid.messages.location", location_mod)
    monkeypatch.setitem(sys.modules, "dtpyodid.messages.operatorid", operatorid_mod)
    monkeypatch.setitem(sys.modules, "dtpyodid.messages.selfid", selfid_mod)
    monkeypatch.setitem(sys.modules, "dtpyodid.messages.system", system_mod)

    return types.SimpleNamespace(
        BasicID=BasicID, Location=Location, OperatorID=OperatorID, SelfID=SelfID, System=System,
    )


def test_extract_message_fields_from_basic_id(monkeypatch):
    classes = _install_fake_dtpyodid(monkeypatch)
    message = classes.BasicID(uas_id="1581F582N9K2K12345\0\0", ua_type=2)
    assert extract_message_fields(message) == {"uas_id": "1581F582N9K2K12345", "ua_type": 2}


def test_extract_message_fields_unwraps_a_tuple_wrapped_ua_type(monkeypatch):
    # Some dtpyodid parses hand back ua_type as a single-element tuple
    # rather than a bare int -- must not leak the tuple into the payload.
    classes = _install_fake_dtpyodid(monkeypatch)
    message = classes.BasicID(uas_id="1581F582N9K2K12345", ua_type=(2,))
    assert extract_message_fields(message)["ua_type"] == 2


def test_extract_message_fields_from_location(monkeypatch):
    classes = _install_fake_dtpyodid(monkeypatch)
    status = types.SimpleNamespace(name="AIRBORNE")
    message = classes.Location(
        latitude=51.5, longitude=-0.1, height=50.0, altitude_geo=100.0, altitude_baro=98.0,
        speed_horizontal=12.5, direction=270.0, status=status,
    )
    assert extract_message_fields(message) == {
        "latitude": 51.5, "longitude": -0.1, "height_m": 50.0, "altitude_geo_m": 100.0,
        "altitude_baro_m": 98.0, "speed_horizontal_mps": 12.5, "direction_deg": 270.0, "status": "AIRBORNE",
    }


def test_extract_message_fields_from_system(monkeypatch):
    classes = _install_fake_dtpyodid(monkeypatch)
    message = classes.System(latitude=51.501, longitude=-0.099)
    assert extract_message_fields(message) == {"operator_latitude": 51.501, "operator_longitude": -0.099}


def test_extract_message_fields_from_operator_id(monkeypatch):
    classes = _install_fake_dtpyodid(monkeypatch)
    message = classes.OperatorID(operator_id="OP12345678\0\0")
    assert extract_message_fields(message) == {"operator_id": "OP12345678"}


def test_extract_message_fields_from_self_id(monkeypatch):
    classes = _install_fake_dtpyodid(monkeypatch)
    message = classes.SelfID(desc="Surveying operation\0\0")
    assert extract_message_fields(message) == {"description": "Surveying operation"}


def test_extract_message_fields_returns_none_for_an_unrecognized_message_type(monkeypatch):
    _install_fake_dtpyodid(monkeypatch)
    assert extract_message_fields(object()) is None
