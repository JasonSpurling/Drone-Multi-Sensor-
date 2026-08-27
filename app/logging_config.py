"""Logging setup: human-readable text (default) or structured JSON, one
object per line -- suited to log aggregators (ELK/Loki/CloudWatch/etc).
Toggle with DRONE_LOG_FORMAT=json.
"""

from __future__ import annotations

import json
import logging

from app.config import LOG_FORMAT, LOG_LEVEL


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    if LOG_FORMAT == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    root.handlers = [handler]
