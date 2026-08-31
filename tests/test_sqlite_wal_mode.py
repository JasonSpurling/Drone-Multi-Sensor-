"""app.db's SQLite connections use WAL (write-ahead logging) instead of
the default rollback-journal mode -- readers (GET /api/tracks etc.,
polled every few seconds by every connected dashboard) no longer block
behind a writer (a continuously-ingesting POST /api/detections) or vice
versa. See app.db._configure_sqlite_connection's "connect" event hook.
"""

from sqlalchemy import text

from app.db import db_session


def test_sqlite_connections_use_wal_journal_mode(isolated_db):
    with db_session() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"


def test_sqlite_foreign_keys_are_still_enabled(isolated_db):
    # Regression guard: the WAL pragma was added to the same "connect"
    # event hook that already turned foreign keys on -- make sure that
    # didn't get dropped in the process.
    with db_session() as conn:
        enabled = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert enabled == 1
