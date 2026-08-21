"""Friendly-track allowlist: a detection carrying raw_data.operator_id and
raw_data.signature is treated as a FRIENDLY vote in classification fusion
(app/fusion.py) only if that signature verifies against the registered
operator's Ed25519 public key (app/remote_id.py) -- an operator_id string
by itself proves nothing.
"""

from __future__ import annotations

from app.db import get_authorized_operator
from app.models import Detection
from app.remote_id import verify_detection_signature


def is_authorized_detection(detection: Detection) -> bool:
    if not detection.raw_data:
        return False
    operator_id = detection.raw_data.get("operator_id")
    signature = detection.raw_data.get("signature")
    if not operator_id or not signature:
        return False

    if detection.site_id is None:
        return False
    operator = get_authorized_operator(str(operator_id), detection.site_id)
    if operator is None or not operator.get("public_key"):
        return False

    return verify_detection_signature(str(operator_id), detection, str(signature), operator["public_key"])
