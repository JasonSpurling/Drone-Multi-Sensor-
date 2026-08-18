"""Outbound webhook alerting: POST a JSON payload to each configured URL
whenever an incident opens. Best-effort and synchronous with a short
timeout -- a slow or dead webhook shouldn't be able to stall detection
ingestion for long, but this does briefly block the request thread that
opened the incident. A production deployment with strict latency
requirements would push this onto a queue instead.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app.config import WEBHOOK_TIMEOUT_SECONDS, WEBHOOK_URLS
from app.models import Incident

logger = logging.getLogger(__name__)


def notify_incident(incident: Incident) -> None:
    if not WEBHOOK_URLS:
        return

    payload = json.dumps(incident.model_dump(mode="json")).encode()
    for url in WEBHOOK_URLS:
        try:
            request = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS)
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("Webhook notification to %s failed: %s", url, exc)
