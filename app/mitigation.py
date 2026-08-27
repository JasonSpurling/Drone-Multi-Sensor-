"""Notifies a downstream counter-UAS mitigation system (RF jammer, net
gun, interdiction platform, ...) when a high-enough-severity incident
opens, carrying the offending track's current position and classification
alongside the incident -- the information such a system needs to actually
act, not just "something happened" the way app/alerting.py's human-facing
channels only need.

This app does not own, drive, or claim any authority over mitigation
hardware -- it's a notifier, structurally identical to app/alerting.py
(severity-gated, best-effort, short-timeout webhook POST), just aimed at
an automated system instead of a person. What a receiving system does
with the notification -- jam, track-and-follow, ignore -- is entirely its
own decision and its own legal/operational responsibility, not this app's.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app.alerting import meets_severity_threshold
from app.config import ALERT_TIMEOUT_SECONDS, MITIGATION_MIN_SEVERITY, MITIGATION_WEBHOOK_URL
from app.models import Incident, Track

logger = logging.getLogger(__name__)


def build_mitigation_payload(incident: Incident, track: Track) -> dict:
    """Pure payload construction, split out from notify_mitigation_system
    for unit testing without a network call."""
    return {
        "incident_uid": incident.incident_uid,
        "incident_type": incident.incident_type.value,
        "severity": incident.severity.value,
        "opened_at": incident.opened_at.isoformat(),
        "track": {
            "track_uid": track.track_uid,
            "classification": track.classification.value,
            "latitude": track.latitude,
            "longitude": track.longitude,
            "altitude_m": track.altitude_m,
            "heading_deg": track.heading_deg,
            "speed_mps": track.speed_mps,
        },
    }


def notify_mitigation_system(incident: Incident, track: Track) -> None:
    if not MITIGATION_WEBHOOK_URL or not meets_severity_threshold(incident.severity, MITIGATION_MIN_SEVERITY):
        return
    if track.latitude is None or track.longitude is None:
        # Nothing for a mitigation system to act on yet -- see
        # app/adapters/lattice.py's identical skip for the same reason.
        return

    payload = build_mitigation_payload(incident, track)
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        MITIGATION_WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(request, timeout=ALERT_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("Mitigation system notification failed: %s", exc)
