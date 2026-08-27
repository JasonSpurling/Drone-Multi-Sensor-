"""End-to-end coverage for scripts/backup.sh and scripts/restore.sh --
these run unattended (a systemd timer, a cron job), so a regression in
them isn't caught by anyone reading output at the time it breaks. Runs the
real scripts as subprocesses against real databases, not a reimplementation
of their logic in Python.
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore.sh"


def _run(script: Path, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), *args], env=env, capture_output=True, text=True, timeout=30
    )


def test_sqlite_backup_then_restore_round_trips_data(tmp_path):
    db_path = tmp_path / "drone_sensor.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t(id INTEGER, name TEXT)")
    conn.execute("INSERT INTO t VALUES (1, 'a')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"
    env = {**os.environ, "DRONE_DATABASE_URL": f"sqlite:///{db_path}", "BACKUP_DIR": str(backup_dir)}

    result = _run(BACKUP_SCRIPT, [], env)
    assert result.returncode == 0, result.stderr
    backups = list(backup_dir.glob("drone_sensor_*.db"))
    assert len(backups) == 1

    # Corrupt/lose the live database, then restore from the backup.
    db_path.unlink()
    result = _run(RESTORE_SCRIPT, [str(backups[0])], env)
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT * FROM t").fetchall() == [(1, "a")]
    conn.close()


def test_sqlite_backup_prunes_down_to_keep_count(tmp_path):
    db_path = tmp_path / "drone_sensor.db"
    sqlite3.connect(db_path).close()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Pre-seed 3 fake backups with distinct mtimes (older than anything
    # this test run will create) so pruning has something to actually prune.
    import time

    for i in range(3):
        fake = backup_dir / f"drone_sensor_2020010{i}T000000Z.db"
        shutil.copy(db_path, fake)
        os.utime(fake, (time.time() - (100 - i), time.time() - (100 - i)))

    env = {
        **os.environ,
        "DRONE_DATABASE_URL": f"sqlite:///{db_path}",
        "BACKUP_DIR": str(backup_dir),
        "BACKUP_KEEP_COUNT": "2",
    }
    result = _run(BACKUP_SCRIPT, [], env)
    assert result.returncode == 0, result.stderr

    remaining = sorted(backup_dir.glob("drone_sensor_*.db"))
    # 3 pre-seeded + 1 just-created = 4, pruned down to the newest 2.
    assert len(remaining) == 2


def test_restore_refuses_a_sqlite_backup_over_a_postgres_url(tmp_path):
    fake_backup = tmp_path / "drone_sensor_20260101T000000Z.db"
    fake_backup.write_bytes(b"not a real sqlite file, doesn't matter for this check")
    env = {**os.environ, "DRONE_DATABASE_URL": "postgresql+psycopg2://user:pass@host/db"}

    result = _run(RESTORE_SCRIPT, [str(fake_backup)], env)

    assert result.returncode != 0
    assert "Refusing" in result.stderr


def test_restore_refuses_a_postgres_backup_over_a_sqlite_url(tmp_path):
    fake_backup = tmp_path / "drone_sensor_20260101T000000Z.dump"
    fake_backup.write_bytes(b"not a real pg_dump file, doesn't matter for this check")
    env = {**os.environ, "DRONE_DATABASE_URL": f"sqlite:///{tmp_path / 'drone_sensor.db'}"}

    result = _run(RESTORE_SCRIPT, [str(fake_backup)], env)

    assert result.returncode != 0
    assert "Refusing" in result.stderr


def test_backup_is_a_no_op_when_no_sqlite_database_exists_yet(tmp_path):
    env = {
        **os.environ,
        "DRONE_DATABASE_URL": f"sqlite:///{tmp_path / 'does_not_exist.db'}",
        "BACKUP_DIR": str(tmp_path / "backups"),
    }
    result = _run(BACKUP_SCRIPT, [], env)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "backups").exists() or not list((tmp_path / "backups").glob("*"))


@pytest.mark.skipif(
    not os.getenv("DRONE_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL, and pg_dump/pg_restore on PATH",
)
def test_postgresql_backup_then_restore_round_trips_data(tmp_path):
    import sqlalchemy

    database_url = os.environ["DRONE_TEST_DATABASE_URL"]
    engine = sqlalchemy.create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("DROP TABLE IF EXISTS t"))
        conn.execute(sqlalchemy.text("CREATE TABLE t(id INTEGER, name TEXT)"))
        conn.execute(sqlalchemy.text("INSERT INTO t VALUES (1, 'a')"))
    engine.dispose()

    backup_dir = tmp_path / "backups"
    env = {**os.environ, "DRONE_DATABASE_URL": database_url, "BACKUP_DIR": str(backup_dir)}

    result = _run(BACKUP_SCRIPT, [], env)
    assert result.returncode == 0, result.stderr
    backups = list(backup_dir.glob("drone_sensor_*.dump"))
    assert len(backups) == 1

    engine = sqlalchemy.create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("DROP TABLE t"))
    engine.dispose()

    result = _run(RESTORE_SCRIPT, [str(backups[0])], env)
    assert result.returncode == 0, result.stderr

    engine = sqlalchemy.create_engine(database_url)
    with engine.begin() as conn:
        rows = conn.execute(sqlalchemy.text("SELECT * FROM t")).all()
    engine.dispose()
    assert rows == [(1, "a")]
