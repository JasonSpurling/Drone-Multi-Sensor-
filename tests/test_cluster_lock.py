import pytest
from sqlalchemy import text

from app.cluster_lock import cluster_association_lock


def test_is_a_noop_on_sqlite(isolated_db):
    if isolated_db.dialect.name != "sqlite":
        pytest.skip("SQLite-specific: this test's assertion is about the no-op path")
    entered = False
    with cluster_association_lock():
        entered = True
    assert entered


def test_actually_serializes_across_connections_on_postgres(isolated_db):
    if isolated_db.dialect.name != "postgresql":
        pytest.skip("Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL")

    # A second, independent connection simulates a second app replica.
    other_conn = isolated_db.connect()
    try:
        with cluster_association_lock():
            # While held, a non-blocking try-lock on the same key from a
            # different connection must fail to acquire it.
            got_it = other_conn.execute(
                text("SELECT pg_try_advisory_lock(7262871001)")
            ).scalar()
            assert got_it is False

        # Released after the context manager exits -- now acquirable.
        got_it = other_conn.execute(text("SELECT pg_try_advisory_lock(7262871001)")).scalar()
        assert got_it is True
        other_conn.execute(text("SELECT pg_advisory_unlock(7262871001)"))
    finally:
        other_conn.close()
