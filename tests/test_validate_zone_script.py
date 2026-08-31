"""app/adapters/validate_zone.py -- run as a real subprocess (matching
tests/test_rotate_api_key_script.py's convention), since a CLI's own
argparse wiring/exit codes are exactly what a test importing its
functions directly would skip over.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SQUARE = '[[51.49,-0.11],[51.49,-0.09],[51.51,-0.09],[51.51,-0.11]]'
BOWTIE = '[[51.0,-0.1],[51.2,0.1],[51.0,0.1],[51.2,-0.1]]'


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.adapters.validate_zone", *args],
        capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
    )


def test_check_a_valid_polygon_exits_zero():
    result = _run("check", "--name", "Square", "--polygon", SQUARE)
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_check_a_self_intersecting_polygon_exits_nonzero():
    result = _run("check", "--name", "Bowtie", "--polygon", BOWTIE)
    assert result.returncode == 1
    assert "self-intersecting" in result.stdout


def test_check_malformed_json_exits_nonzero_with_a_clear_message():
    result = _run("check", "--polygon", "not json")
    assert result.returncode != 0
    assert "not valid JSON" in result.stderr


def test_check_file_validates_every_zone_in_a_seed_file(tmp_path):
    seed_file = tmp_path / "zones.json"
    seed_file.write_text(json.dumps([
        {"name": "Good", "zone_type": "restricted", "polygon": json.loads(SQUARE)},
        {"name": "Bad", "zone_type": "restricted", "polygon": json.loads(BOWTIE)},
    ]))
    result = _run("check-file", str(seed_file))
    assert result.returncode == 1
    assert "OK: 'Good'" in result.stdout
    assert "INVALID: 'Bad'" in result.stdout


def test_add_without_write_only_validates():
    result = _run("add", "--name", "Test Zone", "--zone-type", "restricted", "--polygon", SQUARE)
    assert result.returncode == 0
    assert "Pass --write" in result.stdout


def test_add_with_write_appends_to_a_new_file(tmp_path):
    seed_file = tmp_path / "zones.json"
    result = _run(
        "add", "--name", "New Zone", "--zone-type", "restricted", "--polygon", SQUARE,
        "--max-altitude-m", "120", "--write", str(seed_file),
    )
    assert result.returncode == 0
    entries = json.loads(seed_file.read_text())
    assert len(entries) == 1
    assert entries[0]["name"] == "New Zone"
    assert entries[0]["max_altitude_m"] == 120.0
    assert entries[0]["polygon"] == json.loads(SQUARE)


def test_add_with_write_appends_to_an_existing_file_without_disturbing_others(tmp_path):
    seed_file = tmp_path / "zones.json"
    seed_file.write_text(json.dumps([{"name": "Existing", "zone_type": "restricted", "polygon": json.loads(SQUARE)}]))

    result = _run(
        "add", "--name", "Second Zone", "--zone-type", "monitoring", "--polygon", SQUARE, "--write", str(seed_file),
    )
    assert result.returncode == 0
    entries = json.loads(seed_file.read_text())
    assert [e["name"] for e in entries] == ["Existing", "Second Zone"]


def test_add_with_write_rejects_a_duplicate_name(tmp_path):
    seed_file = tmp_path / "zones.json"
    seed_file.write_text(json.dumps([{"name": "Dup", "zone_type": "restricted", "polygon": json.loads(SQUARE)}]))

    result = _run("add", "--name", "Dup", "--zone-type", "restricted", "--polygon", SQUARE, "--write", str(seed_file))
    assert result.returncode == 1
    assert "already exists" in result.stderr  # raised via SystemExit, not printed to stdout
    assert len(json.loads(seed_file.read_text())) == 1  # unchanged


def test_add_rejects_an_invalid_polygon_before_touching_any_file(tmp_path):
    seed_file = tmp_path / "zones.json"
    result = _run("add", "--name", "Bad", "--zone-type", "restricted", "--polygon", BOWTIE, "--write", str(seed_file))
    assert result.returncode == 1
    assert not seed_file.exists()


def test_the_real_seed_file_still_validates_end_to_end():
    # Regression guard: this repo's own shipped zones.seed.json must
    # always pass, or the validator and the real seed data have drifted.
    result = _run("check-file", str(REPO_ROOT / "app" / "zones.seed.json"))
    assert result.returncode == 0
