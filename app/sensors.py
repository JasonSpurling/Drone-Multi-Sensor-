"""Sensor health: derived from each sensor's most recent detection where
one exists, plus a MISSING entry for any sensor that was deliberately
registered (a known mounting position -- app/api/sensor_registry.py) but
has never actually reported one at all. Without the latter, a sensor
nobody has wired up yet was indistinguishable from one that simply never
existed to this deployment: both were just absent from the result,
whether it had been registered five minutes or five months ago.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from app.config import SENSOR_ONLINE_SECONDS, SENSOR_STALE_SECONDS
from app.db import db_session, list_sensor_registrations
from app.models import SensorHealth, SensorStatus
from app.util import utcnow


def get_sensor_health(site_id: int, now: datetime | None = None) -> list[SensorHealth]:
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
                WHERE site_id = :site_id AND timestamp = (
                    SELECT MAX(timestamp) FROM detection
                    WHERE sensor_id = d.sensor_id AND site_id = :site_id
                )
                ORDER BY sensor_id
                """
            ),
            {"site_id": site_id},
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

    seen_sensor_ids = {row["sensor_id"] for row in rows}
    for registration in list_sensor_registrations(site_id):
        # Only active registrations -- a deliberately decommissioned
        # sensor isn't a gap to flag, and it already stopped mattering to
        # georeferencing the moment it was deactivated (see
        # app/api/sensor_registry.py).
        if not bool(registration["active"]) or registration["sensor_id"] in seen_sensor_ids:
            continue
        results.append(
            SensorHealth(
                sensor_id=registration["sensor_id"],
                sensor_type=registration["sensor_type"],
                last_seen=None,
                status=SensorStatus.MISSING,
            )
        )
    return sorted(results, key=lambda s: s.sensor_id)
