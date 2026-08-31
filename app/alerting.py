"""Severity-routed alert integrations -- Slack, PagerDuty, SMS (via
Twilio), and Meshtastic (off-grid LoRa mesh) -- on top of the generic
webhook fan-out in app/notifications.py.

Each channel is independently configured (empty/unset = disabled) and has
its own minimum-severity threshold: an escalation policy, so e.g. Slack can
get every incident for situational awareness while PagerDuty only pages for
high+ severity and SMS is reserved for critical. This mirrors how real
on-call tooling is set up -- not every alert should page someone's phone.

Every channel is best-effort and synchronous with a short timeout, same
tradeoff as the generic webhook: a slow or dead integration shouldn't be
able to stall detection ingestion for long, but it does briefly block the
request thread that opened the incident. A production deployment with
strict latency requirements would push this onto a queue instead (see
app/queue_publisher.py for an optional additive integration point).
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from app.config import (
    ALERT_TIMEOUT_SECONDS,
    MESHTASTIC_CHANNEL_INDEX,
    MESHTASTIC_HOSTNAME,
    MESHTASTIC_MIN_SEVERITY,
    MESHTASTIC_PORT,
    PAGERDUTY_MIN_SEVERITY,
    PAGERDUTY_ROUTING_KEY,
    SLACK_MIN_SEVERITY,
    SLACK_WEBHOOK_URL,
    SMS_MIN_SEVERITY,
    SMS_TO_NUMBERS,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
)
from app.models import Incident, IncidentSeverity

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    IncidentSeverity.LOW: 0,
    IncidentSeverity.MEDIUM: 1,
    IncidentSeverity.HIGH: 2,
    IncidentSeverity.CRITICAL: 3,
}

# PagerDuty's Events API v2 uses its own severity vocabulary, not ours.
_PAGERDUTY_SEVERITY = {
    IncidentSeverity.LOW: "info",
    IncidentSeverity.MEDIUM: "warning",
    IncidentSeverity.HIGH: "error",
    IncidentSeverity.CRITICAL: "critical",
}


def meets_severity_threshold(severity: IncidentSeverity, minimum: str) -> bool:
    try:
        threshold = IncidentSeverity(minimum)
    except ValueError:
        logger.warning("Invalid minimum severity %r; treating channel as disabled", minimum)
        return False
    return _SEVERITY_RANK[severity] >= _SEVERITY_RANK[threshold]


def _post_json(url: str, payload: dict, headers: dict | None = None) -> None:
    data = json.dumps(payload).encode()
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
    urllib.request.urlopen(request, timeout=ALERT_TIMEOUT_SECONDS)


def _summary(incident: Incident) -> str:
    return incident.description or f"{incident.incident_type.value} (track {incident.track_id})"


def notify_slack(incident: Incident) -> None:
    if not SLACK_WEBHOOK_URL or not meets_severity_threshold(incident.severity, SLACK_MIN_SEVERITY):
        return
    text = f":rotating_light: *{incident.severity.value.upper()}* {_summary(incident)}"
    try:
        _post_json(SLACK_WEBHOOK_URL, {"text": text})
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("Slack alert failed: %s", exc)


def notify_pagerduty(incident: Incident) -> None:
    if not PAGERDUTY_ROUTING_KEY or not meets_severity_threshold(incident.severity, PAGERDUTY_MIN_SEVERITY):
        return
    payload = {
        "routing_key": PAGERDUTY_ROUTING_KEY,
        "event_action": "trigger",
        "dedup_key": incident.incident_uid,
        "payload": {
            "summary": _summary(incident),
            "severity": _PAGERDUTY_SEVERITY[incident.severity],
            "source": f"drone-multi-sensor/track-{incident.track_id}",
            "custom_details": {
                "incident_type": incident.incident_type.value,
                "track_id": incident.track_id,
                "zone_id": incident.zone_id,
            },
        },
    }
    try:
        _post_json("https://events.pagerduty.com/v2/enqueue", payload)
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("PagerDuty alert failed: %s", exc)


def notify_sms(incident: Incident) -> None:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER and SMS_TO_NUMBERS):
        return
    if not meets_severity_threshold(incident.severity, SMS_MIN_SEVERITY):
        return

    body = f"[{incident.severity.value.upper()}] {_summary(incident)}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    credentials = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for to_number in SMS_TO_NUMBERS:
        data = urllib.parse.urlencode({"From": TWILIO_FROM_NUMBER, "To": to_number, "Body": body}).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            urllib.request.urlopen(request, timeout=ALERT_TIMEOUT_SECONDS)
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("SMS alert to %s failed: %s", to_number, exc)


def _send_meshtastic_text(text: str) -> None:
    """Isolated from notify_meshtastic() below purely so tests can
    monkeypatch this one function instead of needing the real
    (optional, requirements-meshtastic.txt) `meshtastic` package
    installed -- the same reason every other lazy-imported optional
    dependency in this app's adapters keeps its actual library call in
    its own small function.
    """
    import meshtastic.tcp_interface

    interface = meshtastic.tcp_interface.TCPInterface(MESHTASTIC_HOSTNAME, portNumber=MESHTASTIC_PORT)
    try:
        interface.sendText(text, channelIndex=MESHTASTIC_CHANNEL_INDEX)
    finally:
        interface.close()


def notify_meshtastic(incident: Incident) -> None:
    if not MESHTASTIC_HOSTNAME or not meets_severity_threshold(incident.severity, MESHTASTIC_MIN_SEVERITY):
        return
    # sendText's real cap (mesh_pb2.Constants.DATA_PAYLOAD_LEN, not a
    # literal number in the library's own public docs) is short -- trimmed
    # here rather than letting the library reject an over-length message
    # outright and lose the alert entirely.
    text = f"[{incident.severity.value.upper()}] {_summary(incident)}"[:200]
    try:
        _send_meshtastic_text(text)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see comment below
        # meshtastic's own exception surface (MeshInterface.MeshInterfaceError
        # for an over-length payload, plus whatever the underlying TCP
        # connection to the node raises on failure/timeout) isn't narrow or
        # fully documented -- same posture as app/adapters/asterix_bridge.py's
        # identically broad except around a similarly under-documented
        # third-party parser: a connectivity or library-internal failure
        # here must not crash incident creation, and there's no safe
        # narrower exception list to trust.
        logger.warning("Meshtastic alert failed: %s", exc)


def notify_escalations(incident: Incident) -> None:
    """Fan out to every configured severity-routed channel. Each channel
    independently no-ops if it isn't configured or the incident doesn't
    meet its threshold -- callers don't need to check anything first.
    """
    notify_slack(incident)
    notify_pagerduty(incident)
    notify_sms(incident)
    notify_meshtastic(incident)
