"""app.db's SQLite connections use WAL (write-ahead logging) instead of
the default rollback-journal mode -- readers (GET /api/tracks etc.,
polled every few seconds by every connected dashboard) no longer block
behind a writer (a continuously-ingesting POST /api/detections) or vice
versa. See app.db._configure_sqlite_connection's "connect" event hook.
"""

import os

import pytest
from sqlalchemy import text

from app.db import db_session

# These PRAGMAs are SQLite-specific -- when the suite is pointed at
# PostgreSQL (DRONE_TEST_DATABASE_URL, see tests/conftest.py), there's no
# SQLite connection at all to assert this about, the same reasoning
# test_cluster_lock.py's own SQLite-only test skips under Postgres for.
_using_postgres = os.getenv("DRONE_TEST_DATABASE_URL", "").startswith("postgresql")


@pytest.mark.skipif(_using_postgres, reason="WAL journal mode is a SQLite-specific pragma")
def test_sqlite_connections_use_wal_journal_mode(isolated_db):
    with db_session() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"


@pytest.mark.skipif(_using_postgres, reason="foreign_keys is a SQLite-specific pragma")
def test_sqlite_foreign_keys_are_still_enabled(isolated_db):
    # Regression guard: the WAL pragma was added to the same "connect"
    # event hook that already turned foreign keys on -- make sure that
    # didn't get dropped in the process.
    with db_session() as conn:
        enabled = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert enabled == 1
