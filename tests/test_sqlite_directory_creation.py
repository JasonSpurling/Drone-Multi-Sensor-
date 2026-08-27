"""app.db.ensure_sqlite_directory_exists -- a fresh checkout's data/
directory doesn't exist yet (gitignored, and git doesn't track empty
directories anyway), so without this the very first connection attempt
fails with "unable to open database file" before init_db() ever runs.
Regression test for exactly that: a real user hit this on a completely
fresh clone.
"""

from app.db import ensure_sqlite_directory_exists


def test_creates_a_missing_parent_directory(tmp_path):
    db_path = tmp_path / "does" / "not" / "exist" / "yet" / "drone_sensor.db"
    assert not db_path.parent.exists()

    ensure_sqlite_directory_exists(f"sqlite:///{db_path}")

    assert db_path.parent.is_dir()


def test_is_a_noop_when_the_directory_already_exists(tmp_path):
    db_path = tmp_path / "drone_sensor.db"
    assert tmp_path.exists()

    ensure_sqlite_directory_exists(f"sqlite:///{db_path}")  # must not raise

    assert tmp_path.is_dir()


def test_is_a_noop_for_an_in_memory_database():
    # No file or directory to create for ":memory:" -- must not raise or
    # try to create a literal directory named ":memory:".
    ensure_sqlite_directory_exists("sqlite:///:memory:")


def test_is_a_noop_for_a_non_sqlite_url():
    # A postgresql:// URL has no local file/directory at all -- must not
    # attempt to interpret any part of it as a filesystem path.
    ensure_sqlite_directory_exists("postgresql+psycopg2://user:pass@localhost:5432/dbname")
