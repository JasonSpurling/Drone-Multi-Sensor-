"""Cryptographic Remote ID verification.

An authorized operator registers an Ed25519 public key (see
app.db.authorized_operator, PUT /api/authorized-operators/{operator_id}).
A detection claiming that operator_id must carry a signature -- produced
by the operator's *private* key -- over its own canonical fields
(operator_id, sensor_id, timestamp, position). Only a detection with a
signature that verifies against the registered public key gets treated as
a genuine claim (see app/allowlist.py).

This closes the spoofing gap the security review flagged: previously any
detection whose raw_data.operator_id matched a registered value was
trusted outright, so an attacker holding only an ingest-role API key
could fabricate that claim for any known/guessed operator_id. Now they'd
need the operator's private key, which never leaves the operator's
possession -- only the public key is ever registered with this system.

Scope note: this implements the cryptographic trust layer real Remote ID
systems need (is this operator_id claim genuine?), not the ASTM F3411
broadcast wire format (Bluetooth/Wi-Fi Neighbor Awareness Networking
framing real Remote ID beacons use over the air). This tracker ingests
structured detections over HTTP, not raw RF broadcasts, so that framing
doesn't apply here -- a real deployment would have a bridge adapter (like
app/adapters/sbs1.py or dump1090_bridge.py) that receives actual Remote ID
broadcasts, extracts the operator_id/signature fields the standard
defines, and posts them through this same verification path.
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.models import Detection


def canonical_message(operator_id: str, detection: Detection) -> bytes:
    """The exact bytes a signature must cover -- binds the operator_id
    claim to this specific detection's sensor, time, and position, so a
    captured signature can't be replayed against a different detection.
    Position is rounded to 6 decimal degrees (~11cm) so signer and
    verifier always agree on the exact bytes regardless of incidental
    floating-point formatting differences.
    """
    lat = round(detection.latitude, 6) if detection.latitude is not None else None
    lon = round(detection.longitude, 6) if detection.longitude is not None else None
    parts = [operator_id, detection.sensor_id, detection.timestamp.isoformat(), str(lat), str(lon)]
    return "|".join(parts).encode()


def generate_keypair() -> tuple[str, str]:
    """Returns (private_key_b64, public_key_b64) for a new Remote ID
    identity. The private key must stay with the operator/sensor that
    signs detections; only the public key gets registered via
    PUT /api/authorized-operators/{operator_id}.
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(private_bytes).decode(), base64.b64encode(public_bytes).decode()


def sign_detection(operator_id: str, detection: Detection, private_key_b64: str) -> str:
    """Returns a base64-encoded signature to place in
    raw_data['signature'] alongside raw_data['operator_id'].
    """
    private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    signature = private_key.sign(canonical_message(operator_id, detection))
    return base64.b64encode(signature).decode()


def verify_detection_signature(
    operator_id: str, detection: Detection, signature_b64: str, public_key_b64: str
) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        public_key.verify(base64.b64decode(signature_b64), canonical_message(operator_id, detection))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
