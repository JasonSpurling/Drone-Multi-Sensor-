"""app.db's module-level engine setup: QueuePool kwargs only apply to a
real (PostgreSQL) connection pool (SQLite doesn't use QueuePool, and
passing those kwargs to an engine using a pool class that doesn't accept
them raises TypeError), and the "connect" event hook that sets SQLite's
WAL journal mode + foreign keys pragma.

Both are decided once, at import time, from DRONE_DATABASE_URL -- unlike
most of this app's config (read fresh per-request via os.getenv). tests/
conftest.py's isolated_db fixture works around this for the rest of the
suite by building its own separate test Engine and monkeypatching
app.db.engine, rather than exercising app.db's own module-level engine
directly (see that fixture's docstring) -- so this file, like
test_cors.py before it, reloads the real module to actually exercise
this import-time branch, following the same pattern.
"""

import importlib
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text


@pytest.fixture
def reload_db_module(monkeypatch):
    """Reloads app.config then app.db with DRONE_DATABASE_URL set to
    `url`, returning the freshly-reloaded app.db module. Always restores
    both modules to their normal (sqlite, isolated_db-monkeypatched)
    state afterward -- the rest of the suite depends on app.db.engine
    being whatever isolated_db substituted in, not this test's real one.
    """
    def _reload(url: str):
        monkeypatch.setenv("DRONE_DATABASE_URL", url)
        import app.config
        import app.db

        importlib.reload(app.config)
        return importlib.reload(app.db)

    yield _reload

    monkeypatch.delenv("DRONE_DATABASE_URL", raising=False)
    import app.config
    import app.db

    importlib.reload(app.config)
    importlib.reload(app.db)


def test_postgresql_url_configures_queuepool_kwargs(reload_db_module, monkeypatch):
    monkeypatch.setenv("DRONE_DB_POOL_SIZE", "7")
    monkeypatch.setenv("DRONE_DB_MAX_OVERFLOW", "13")
    db_module = reload_db_module("postgresql+psycopg2://user:pass@nonexistent-host/db")

    # create_engine() itself never connects (SQLAlchemy engines are lazy)
    # -- constructing one against an unreachable host is enough to check
    # the pool was actually configured with these kwargs, no real
    # PostgreSQL server needed here.
    assert db_module.engine.pool.size() == 7
    assert db_module.engine.pool._max_overflow == 13


def test_sqlite_url_never_passes_queuepool_kwargs_to_the_engine(reload_db_module):
    # The opposite regression: SQLite doesn't use QueuePool at all, so
    # passing pool_size/max_overflow to create_engine() for a sqlite:///
    # URL raises TypeError -- this just has to succeed without one.
    with tempfile.TemporaryDirectory() as tmp:
        db_module = reload_db_module(f"sqlite:///{tmp}/engine_setup_test.db")
        assert db_module.engine.dialect.name == "sqlite"


def test_sqlite_connect_hook_sets_wal_and_foreign_keys(reload_db_module):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "engine_setup_test.db"
        db_module = reload_db_module(f"sqlite:///{db_path}")

        with db_module.engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
