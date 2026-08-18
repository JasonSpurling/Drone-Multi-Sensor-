"""Friendly-track allowlist: if a detection carries a raw_data.operator_id
matching a known/authorized operator (app.db.authorized_operator -- e.g.
an FAA Remote ID broadcast operator ID), it's treated as a FRIENDLY vote
in classification fusion (see app/fusion.py) rather than an unknown
threat.

Limitation: this trusts whatever the detection claims as its operator_id
at face value -- there's no cryptographic verification here (Remote ID
broadcasts aren't signed), so a well-resourced adversary could spoof an
authorized operator_id to get a hostile drone classified FRIENDLY. That's
a real gap no amount of software alone closes; a serious deployment would
pair this with a verified/cryptographic identity channel.
"""

from __future__ import annotations

from app.db import is_authorized_operator
from app.models import Detection


def is_authorized_detection(detection: Detection) -> bool:
    if not detection.raw_data:
        return False
    operator_id = detection.raw_data.get("operator_id")
    if not operator_id:
        return False
    return is_authorized_operator(str(operator_id))
