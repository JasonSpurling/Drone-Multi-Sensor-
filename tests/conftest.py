"""Shared pytest fixtures: every test gets its own on-disk SQLite database
so tests never share state or touch a real data/drone_sensor.db.

Set DRONE_TEST_DATABASE_URL to point the suite at a different backend (e.g.
a PostgreSQL instance) to run the exact same tests against it -- see
tests/test_postgres_backend.py for the dedicated parity tests, and the
"Tests" section of the README for how to run the full suite that way.
"""

import os

import pytest
from sqlalchemy import create_engine, event, text


def _make_engine(url: str):
    test_engine = create_engine(url, future=True)
    if test_engine.dialect.name == "sqlite":
        @event.listens_for(test_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()
    return test_engine


def _reset_postgres_schema(test_engine) -> None:
    with test_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    override_url = os.getenv("DRONE_TEST_DATABASE_URL")
    if override_url:
        test_engine = _make_engine(override_url)
        _reset_postgres_schema(test_engine)
    else:
        db_path = tmp_path / "test.db"
        test_engine = _make_engine(f"sqlite:///{db_path}")

    monkeypatch.setattr("app.db.engine", test_engine)

    from app.db import init_db
    from app.sites import reset_cache_for_tests

    # Each test gets a brand-new database -- a default site id cached from
    # a *previous* test's (now-disposed) database would be stale here and
    # violate the new database's site.id foreign key.
    reset_cache_for_tests()
    init_db()
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def site_id(isolated_db) -> int:
    """The default site's id in this test's isolated database -- most
    tests only care about one site and can just pass this straight
    through to whatever they're constructing/calling.
    """
    from app.sites import ensure_default_site

    return ensure_default_site()
