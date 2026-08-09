"""Sensor health derived from each sensor's most recent detection."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import SENSOR_ONLINE_SECONDS, SENSOR_STALE_SECONDS
from app.db import db_session
from app.models import SensorHealth, SensorStatus


def get_sensor_health(now: datetime | None = None) -> list[SensorHealth]:
    now = now or datetime.utcnow()

    # SQLite guarantees that when a query has exactly one MAX() aggregate,
    # any other bare (non-aggregated) columns come from the same row as the
    # max, so sensor_type is correctly paired with each sensor's newest row.
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT sensor_id, sensor_type, MAX(timestamp) AS timestamp
            FROM detection
            GROUP BY sensor_id
            ORDER BY sensor_id
            """
        ).fetchall()

    results: list[SensorHealth] = []
    for row in rows:
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
                sensor_id=row["sensor_id"],
                sensor_type=row["sensor_type"],
                last_seen=last_seen,
                status=status,
            )
        )
    return results
