"""Cross-process serialization for app.tracking's detection-association
critical section, for real horizontal-scaling HA.

app.tracking._association_lock (a plain threading.Lock) only serializes
concurrent requests *within one process* -- correct for the common
single-replica deployment, but not enough for redundancy: run two app
replicas behind a load balancer, both backed by the same PostgreSQL
database, and two detections landing on different replicas at the same
instant can each miss the other's in-flight track/incident and create a
duplicate track -- exactly the race the in-process lock exists to prevent
within one process, just one level up.

cluster_association_lock() closes that gap on PostgreSQL by also taking a
session-level advisory lock (pg_advisory_lock) held for the same critical
section, so only one replica's request can be inside it at a time,
cluster-wide. On SQLite -- which this app treats as inherently single-
process/single-deployment anyway (see app/ratelimit.py's own docstring on
the same limitation for the in-memory rate limiter) -- there's no
cross-process case to guard, so this degrades to a no-op; the in-process
lock alone is already correct there.

Looks up the engine's dialect at call time (via `app.db.engine`, not a
module-load-time import) for the same reason app/api/health.py does: the
test suite's isolated-database fixture swaps the engine after import.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app import db as db_module

# Arbitrary fixed key identifying "the detection-association critical
# section" among any other advisory locks this deployment might someday
# take. Only one such section exists today, so one constant is enough --
# pg_advisory_lock's key is scoped to the whole database, not any one
# table, so this doesn't need to relate to a specific row/table.
_ASSOCIATION_LOCK_KEY = 7_262_871_001


@contextmanager
def cluster_association_lock() -> Iterator[None]:
    if db_module.engine.dialect.name != "postgresql":
        yield
        return

    conn = db_module.engine.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _ASSOCIATION_LOCK_KEY})
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ASSOCIATION_LOCK_KEY})
    finally:
        conn.close()
