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


def _track_column_names() -> set[str]:
    inspector = inspect(db.engine)
    return {col["name"] for col in inspector.get_columns("track")}


def test_migration_adds_columns_to_a_table_that_predates_them(isolated_db):
    # Simulate an existing deployment's track table from before
    # heading_deg/speed_mps/... existed: drop a couple of them, then
    # re-run init_db (exactly what happens on every app startup) and
    # confirm they come back -- proving _migrate_table_columns actually
    # does the work, not just schema.py's current definition being picked
    # up by create_all on a fresh table.
    with db.engine.begin() as conn:
        # SQLite (the test default) supports DROP COLUMN since 3.35;
        # PostgreSQL always has too -- both backends this app supports.
        conn.execute(text("ALTER TABLE track DROP COLUMN heading_deg"))
        conn.execute(text("ALTER TABLE track DROP COLUMN speed_mps"))
    assert "heading_deg" not in _track_column_names()
    assert "speed_mps" not in _track_column_names()

    db.init_db()

    assert "heading_deg" in _track_column_names()
    assert "speed_mps" in _track_column_names()


def test_migration_skips_a_table_that_does_not_exist_yet(isolated_db):
    # _TABLE_MIGRATION_COLUMNS also names tables from other subsystems
    # (authorized_operator, zone, ...) -- init_db() (which always creates
    # every current table first via metadata.create_all) means none of
    # them are ever actually missing in practice, but the column-migration
    # step still has to tolerate one being absent rather than erroring,
    # for a hypothetical partial/older schema. Drop one here (a table
    # nothing else in this test needs) to actually exercise that branch,
    # rather than just calling the function with every table present.
    with db.engine.begin() as conn:
        # CASCADE: PostgreSQL (unlike SQLite) enforces the incident.zone_id
        # foreign key and refuses a plain DROP TABLE while it still exists
        # -- SQLite has no CASCADE keyword on DROP TABLE at all.
        drop_sql = "DROP TABLE zone CASCADE" if db.engine.dialect.name == "postgresql" else "DROP TABLE zone"
        conn.execute(text(drop_sql))

    db._migrate_table_columns()  # must not raise despite "zone" being in the migration map but absent

    inspector = inspect(db.engine)
    assert "zone" not in inspector.get_table_names()  # untouched, not recreated by this step


def test_migration_drops_and_recreates_the_old_shaped_kalman_state_table(isolated_db):
    # Simulate a pre-IMM deployment's track_kalman_state table (the old
    # single constant-velocity filter's x_m/y_m/vx_mps/vy_mps/covariance
    # columns, no `models`/`mode_probabilities`) -- confirms
    # _migrate_kalman_state_table actually detects and replaces the old
    # shape, not just that a fresh table already has the new one.
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE track_kalman_state"))
        conn.execute(
            text(
                "CREATE TABLE track_kalman_state ("
                "track_id INTEGER PRIMARY KEY, x_m REAL, y_m REAL, vx_mps REAL, vy_mps REAL, covariance TEXT)"
            )
        )

    db.init_db()

    inspector = inspect(db.engine)
    columns = {col["name"] for col in inspector.get_columns("track_kalman_state")}
    assert "models" in columns
    assert "x_m" not in columns


def test_migration_leaves_an_already_current_kalman_state_table_alone(isolated_db):
    # The no-op path: a table that already has the new `models` column
    # (true for every fresh init_db()) must not be dropped.
    db._migrate_kalman_state_table()  # must not raise or drop anything
    inspector = inspect(db.engine)
    assert "track_kalman_state" in inspector.get_table_names()


def test_migration_survives_preexisting_duplicate_zone_names(isolated_db, caplog):
    # A database from before the zone(site_id, name) UNIQUE index existed
    # could already have two same-named zones in the same site --
    # deliberately created here by inserting straight past the
    # application-layer uniqueness check (app/api/zones.py). The
    # migration must log a warning and let startup continue, not raise
    # and take the whole app down over pre-existing data it didn't cause.
    with db.engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS idx_zone_site_id_name"))
        for _ in range(2):
            conn.execute(
                text(
                    "INSERT INTO zone (site_id, name, zone_type, polygon, active) "
                    "VALUES (1, 'Duplicate Zone', 'monitoring', '[]', 1)"
                )
            )

    import logging

    with caplog.at_level(logging.WARNING):
        db._migrate_indexes()  # must not raise despite the duplicate rows below

    assert any("Could not create the unique zone" in r.message for r in caplog.records)
