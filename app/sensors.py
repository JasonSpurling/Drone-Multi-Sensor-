"""Sensor health derived from each sensor's most recent detection."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from app.config import SENSOR_ONLINE_SECONDS, SENSOR_STALE_SECONDS
from app.db import db_session
from app.models import SensorHealth, SensorStatus


def get_sensor_health(now: datetime | None = None) -> list[SensorHealth]:
    now = now or datetime.utcnow()

    with db_session() as conn:
        rows = conn.execute(
            "SELECT sensor_id, sensor_type, timestamp FROM detection ORDER BY timestamp DESC"
        ).fetchall()

    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(row["sensor_id"], row)

    results: list[SensorHealth] = []
    for sensor_id in sorted(latest):
        row = latest[sensor_id]
        last_seen = datetime.fromisoformat(row["timestamp"])
        age = now - last_seen
        if age <= timedelta(seconds=SENSOR_ONLINE_SECONDS):
            status = SensorStatus.ONLINE
        elif age <= timedelta(seconds=SENSOR_STALE_SECONDS):
            status = SensorStatus.STALE
        else:
            status = SensorStatus.OFFLINE
        results.append(
            SensorHealth(
                sensor_id=sensor_id,
                sensor_type=row["sensor_type"],
                last_seen=last_seen,
                status=status,
            )
        )
    return results
