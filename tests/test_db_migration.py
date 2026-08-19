from sqlalchemy import inspect, text

import app.db as db


def _track_index_names() -> set[str]:
    # Look up app.db.engine at call time, not import time -- the isolated_db
    # fixture monkeypatches it to a fresh per-test engine, which a plain
    # `from app.db import engine` would miss (it binds the original object).
    inspector = inspect(db.engine)
    return {idx["name"] for idx in inspector.get_indexes("track")}


def test_track_status_last_seen_index_exists_after_init(isolated_db):
    assert "idx_track_status_last_seen" in _track_index_names()


def test_migration_adds_the_index_to_a_table_that_predates_it(isolated_db):
    # Simulate an existing deployment's database that was created before
    # this index existed: drop it, then re-run init_db (exactly what
    # happens on every app startup) and confirm it comes back -- proving
    # _migrate_indexes actually does the work, not just schema.py's
    # definition being picked up by create_all on a fresh table.
    with db.engine.begin() as conn:
        conn.execute(text("DROP INDEX idx_track_status_last_seen"))
    assert "idx_track_status_last_seen" not in _track_index_names()

    db.init_db()

    assert "idx_track_status_last_seen" in _track_index_names()


def test_rerunning_init_db_is_a_safe_no_op(isolated_db):
    db.init_db()
    db.init_db()  # must not raise (CREATE INDEX IF NOT EXISTS, not CREATE INDEX)
    assert "idx_track_status_last_seen" in _track_index_names()
