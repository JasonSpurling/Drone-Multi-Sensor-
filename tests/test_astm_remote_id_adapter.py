from app.adapters.astm_remote_id import build_detection_payload, merge_fields


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
