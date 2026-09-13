"""app.allowlist.is_authorized_detection: whether a detection's
raw_data.operator_id/signature pair earns it a FRIENDLY vote in fusion
(app/fusion.py) -- an operator_id string alone proves nothing, only a
signature that verifies against that operator's registered Ed25519 public
key (app/remote_id.py) does.
"""

from datetime import datetime

from app.allowlist import is_authorized_detection
from app.db import upsert_authorized_operator
from app.models import Detection, SensorType
from app.remote_id import generate_keypair, sign_detection

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_detection(**overrides) -> Detection:
    defaults = {
        "sensor_id": "radar-1", "sensor_type": SensorType.RADAR, "timestamp": BASE_TIME,
        "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }
    defaults.update(overrides)
    return Detection(**defaults)


def test_no_raw_data_is_not_authorized():
    assert is_authorized_detection(make_detection(raw_data=None)) is False


def test_missing_operator_id_or_signature_is_not_authorized():
    assert is_authorized_detection(make_detection(raw_data={"operator_id": "OP-1"})) is False
    assert is_authorized_detection(make_detection(raw_data={"signature": "abc"})) is False


def test_a_detection_with_no_site_id_is_not_authorized(site_id):
    private_key, public_key = generate_keypair()
    upsert_authorized_operator("OP-1", site_id, "Test Operator", public_key)
    detection = make_detection(site_id=None)
    signature = sign_detection("OP-1", detection, private_key)
    detection.raw_data = {"operator_id": "OP-1", "signature": signature}
    assert is_authorized_detection(detection) is False


def test_an_unregistered_operator_is_not_authorized(site_id):
    detection = make_detection(site_id=site_id, raw_data={"operator_id": "OP-unknown", "signature": "abc"})
    assert is_authorized_detection(detection) is False


def test_a_registered_operator_with_no_public_key_is_not_authorized(site_id):
    upsert_authorized_operator("OP-1", site_id, "Test Operator", public_key=None)
    detection = make_detection(site_id=site_id, raw_data={"operator_id": "OP-1", "signature": "abc"})
    assert is_authorized_detection(detection) is False


def test_a_valid_signature_from_a_registered_operator_is_authorized(site_id):
    private_key, public_key = generate_keypair()
    upsert_authorized_operator("OP-1", site_id, "Test Operator", public_key)
    detection = make_detection(site_id=site_id)
    signature = sign_detection("OP-1", detection, private_key)
    detection.raw_data = {"operator_id": "OP-1", "signature": signature}
    assert is_authorized_detection(detection) is True
