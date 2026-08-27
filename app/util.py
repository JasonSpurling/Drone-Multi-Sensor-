"""Small shared helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a naive datetime, matching the naive-UTC
    timestamps stored throughout this app. Replaces datetime.utcnow(),
    deprecated since Python 3.12.
    """
    return datetime.now(UTC).replace(tzinfo=None)
