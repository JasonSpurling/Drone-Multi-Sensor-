"""scripts/rotate_api_key.py -- run as a real subprocess (not its internal
functions imported directly) for generate/add/remove, plus one true
end-to-end test proving the whole pipeline works together: the script's
output file, read via DRONE_API_KEYS_FILE, actually authenticates a real
request through app.auth -- not just that each piece works in isolation.
"""

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "rotate_api_key.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=10
    )


def test_generate_prints_a_key_and_a_valid_json_entry():
    result = _run("generate", "--role", "ingest", "--label", "radar-1")
    assert result.returncode == 0
    assert "New key:" in result.stdout

    json_start = result.stdout.index("{")
    entry = json.loads(result.stdout[json_start:])
    (key, value) = next(iter(entry.items()))
    assert len(key) > 20  # a real random key, not a placeholder
    assert value == {"role": "ingest", "label": "radar-1"}


def test_generate_with_only_a_role_produces_the_bare_string_shape():
    result = _run("generate", "--role", "viewer")
    json_start = result.stdout.index("{")
    entry = json.loads(result.stdout[json_start:])
    (_, value) = next(iter(entry.items()))
    assert value == "viewer"  # pre-multi-site bare-role-string shape, not an object


def test_add_creates_the_file_if_it_does_not_exist_yet(tmp_path):
    keys_file = tmp_path / "keys.json"
    assert not keys_file.exists()

    result = _run("add", str(keys_file), "--role", "admin", "--site", "ops")
    assert result.returncode == 0
    assert keys_file.is_file()

    keys = json.loads(keys_file.read_text())
    assert len(keys) == 1
    (_, value) = next(iter(keys.items()))
    assert value == {"role": "admin", "site": "ops"}


def test_add_appends_to_an_existing_file_without_disturbing_other_keys(tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps({"existing-key": "viewer"}))

    _run("add", str(keys_file), "--role", "ingest")

    keys = json.loads(keys_file.read_text())
    assert len(keys) == 2
    assert keys["existing-key"] == "viewer"


def test_remove_deletes_only_the_named_key(tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps({"key-a": "admin", "key-b": "viewer"}))

    result = _run("remove", str(keys_file), "--key", "key-a")
    assert result.returncode == 0

    keys = json.loads(keys_file.read_text())
    assert keys == {"key-b": "viewer"}


def test_remove_is_a_no_op_for_a_key_not_in_the_file(tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps({"key-a": "admin"}))

    result = _run("remove", str(keys_file), "--key", "not-there")
    assert result.returncode == 0

    keys = json.loads(keys_file.read_text())
    assert keys == {"key-a": "admin"}


def test_end_to_end_a_key_generated_and_added_by_the_script_actually_authenticates(
    tmp_path, monkeypatch, isolated_db
):
    """The real pipeline: run the script as a subprocess to add a key to a
    file, point DRONE_API_KEYS_FILE at it, and make a real authenticated
    request through app.auth -- proving get_api_keys_json() -> the
    script's file -> a working key, end to end.
    """
    keys_file = tmp_path / "keys.json"
    result = _run("add", str(keys_file), "--role", "ingest", "--label", "radar-1")
    assert result.returncode == 0
    new_key = result.stdout.splitlines()[0].removeprefix("New key: ").strip()
    # A second, unrelated key stays in the file throughout -- removing
    # new_key below must leave *that* key still enforcing auth, not empty
    # out the whole configured-keys set (an empty set is documented,
    # correct, pre-existing behavior: "no keys configured" means open
    # access -- a real rotation removing one of several keys should never
    # trigger that).
    _run("add", str(keys_file), "--role", "admin", "--label", "unrelated-key")

    monkeypatch.setenv("DRONE_API_KEYS_FILE", str(keys_file))
    monkeypatch.setattr("app.config.API_KEYS_JSON", "")
    monkeypatch.setattr("app.config.API_KEY", "")

    detection = {
        "sensor_id": "radar-1", "sensor_type": "radar",
        "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }
    with TestClient(app) as client:
        # No key at all -> rejected (auth is actually active).
        assert client.post("/api/detections", json=detection).status_code == 401
        # The freshly generated key -> accepted.
        r = client.post("/api/detections", json=detection, headers={"X-API-Key": new_key})
        assert r.status_code == 201

        # Now rotate: remove it from the file (simulating "nothing uses
        # the old key any more") and confirm it stops working immediately,
        # no restart.
        remove_result = _run("remove", str(keys_file), "--key", new_key)
        assert remove_result.returncode == 0
        r2 = client.post("/api/detections", json=detection, headers={"X-API-Key": new_key})
        assert r2.status_code == 401
