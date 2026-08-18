"""Sensor health derived from each sensor's most recent detection."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from app.config import SENSOR_ONLINE_SECONDS, SENSOR_STALE_SECONDS
from app.db import db_session
from app.models import SensorHealth, SensorStatus
from app.util import utcnow


def get_sensor_health(now: datetime | None = None) -> list[SensorHealth]:
    now = now or utcnow()

    # A correlated-subquery "latest row per group" instead of a bare
    # MAX()-with-ungrouped-columns SELECT: SQLite tolerates the latter (and
    # happens to pair columns from the max row), but PostgreSQL rejects it
    # outright since sensor_type is neither aggregated nor in GROUP BY. This
    # form is standard SQL and portable to both.
    with db_session() as conn:
        rows = conn.execute(
            text(
                """
                SELECT sensor_id, sensor_type, timestamp
                FROM detection d
                WHERE timestamp = (
                    SELECT MAX(timestamp) FROM detection WHERE sensor_id = d.sensor_id
                )
                ORDER BY sensor_id
                """
            )
        ).mappings().all()

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
