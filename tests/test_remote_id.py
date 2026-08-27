from datetime import datetime

from app.models import Detection, SensorType
from app.remote_id import generate_keypair, sign_detection, verify_detection_signature

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(**overrides) -> Detection:
    defaults = {
        "sensor_id": "radar-1", "sensor_type": SensorType.RADAR, "timestamp": BASE_TIME,
        "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }
    defaults.update(overrides)
    return Detection(**defaults)


def test_generate_keypair_returns_distinct_base64_keys():
    private_key, public_key = generate_keypair()
    assert private_key != public_key
    assert len(private_key) > 0 and len(public_key) > 0
    private_key2, public_key2 = generate_keypair()
    assert private_key2 != private_key
    assert public_key2 != public_key


def test_valid_signature_verifies():
    private_key, public_key = generate_keypair()
    detection = make_detection()
    signature = sign_detection("OP-1", detection, private_key)
    assert verify_detection_signature("OP-1", detection, signature, public_key) is True


def test_signature_from_wrong_private_key_fails():
    _, public_key_a = generate_keypair()
    private_key_b, _ = generate_keypair()
    detection = make_detection()
    signature = sign_detection("OP-1", detection, private_key_b)
    assert verify_detection_signature("OP-1", detection, signature, public_key_a) is False


def test_signature_does_not_verify_for_different_operator_id():
    private_key, public_key = generate_keypair()
    detection = make_detection()
    signature = sign_detection("OP-1", detection, private_key)
    assert verify_detection_signature("OP-2", detection, signature, public_key) is False


def test_signature_does_not_verify_after_position_tampering():
    private_key, public_key = generate_keypair()
    detection = make_detection()
    signature = sign_detection("OP-1", detection, private_key)
    tampered = make_detection(latitude=52.0)
    assert verify_detection_signature("OP-1", tampered, signature, public_key) is False


def test_signature_does_not_verify_after_timestamp_tampering():
    private_key, public_key = generate_keypair()
    detection = make_detection()
    signature = sign_detection("OP-1", detection, private_key)
    tampered = make_detection(timestamp=datetime(2026, 1, 1, 13, 0, 0))
    assert verify_detection_signature("OP-1", tampered, signature, public_key) is False


def test_malformed_signature_does_not_crash_verification():
    _, public_key = generate_keypair()
    detection = make_detection()
    assert verify_detection_signature("OP-1", detection, "not-valid-base64!!!", public_key) is False
    assert verify_detection_signature("OP-1", detection, "", public_key) is False


def test_malformed_public_key_does_not_crash_verification():
    private_key, _ = generate_keypair()
    detection = make_detection()
    signature = sign_detection("OP-1", detection, private_key)
    assert verify_detection_signature("OP-1", detection, signature, "not-a-real-key") is False


def test_position_rounding_ignores_insignificant_float_noise():
    private_key, public_key = generate_keypair()
    detection = make_detection(latitude=51.5000001, longitude=-0.1000001)
    signature = sign_detection("OP-1", detection, private_key)
    # Sub-11cm difference in the raw float shouldn't change the signed message.
    almost_identical = make_detection(latitude=51.5000002, longitude=-0.1000002)
    assert verify_detection_signature("OP-1", almost_identical, signature, public_key) is True


def test_signature_verifies_against_position_georeferencing_later_computed():
    # An azimuth/range-only sensor (no GPS of its own) has nothing to sign
    # but None/None for position. app/georeference.py fills in an estimated
    # lat/lon server-side afterwards (and marks georeferenced=True) so the
    # detection can be tracked -- but that estimate was never part of what
    # the sensor signed. Verification must bind to what was actually
    # signed (None/None), or a legitimate signed claim from this whole
    # class of sensor could never verify.
    private_key, public_key = generate_keypair()
    detection = make_detection(latitude=None, longitude=None)
    signature = sign_detection("OP-1", detection, private_key)

    georeferenced = make_detection(latitude=51.5, longitude=-0.1, georeferenced=True)
    assert verify_detection_signature("OP-1", georeferenced, signature, public_key) is True


def test_signature_does_not_verify_after_timestamp_tampering_on_georeferenced_detection():
    # Binding to None/None for a georeferenced detection must not become a
    # blanket exemption from verification -- only position is ignored;
    # every other field is still checked.
    private_key, public_key = generate_keypair()
    detection = make_detection(latitude=None, longitude=None)
    signature = sign_detection("OP-1", detection, private_key)

    tampered = make_detection(
        latitude=51.5, longitude=-0.1, georeferenced=True, timestamp=datetime(2026, 1, 1, 13, 0, 0)
    )
    assert verify_detection_signature("OP-1", tampered, signature, public_key) is False
