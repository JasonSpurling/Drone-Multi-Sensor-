"""Shared pytest fixtures: every test gets its own on-disk SQLite database
so tests never share state or touch a real data/drone_sensor.db.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.db.DB_PATH", db_path)

    from app.db import init_db

    init_db()
    yield db_path
