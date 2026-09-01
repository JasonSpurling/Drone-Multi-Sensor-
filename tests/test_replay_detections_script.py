"""scripts/replay_detections.py's pure functions -- timestamp rewriting,
pacing, and server-field stripping -- loaded the same way as
tests/test_load_test_script.py (importlib, since scripts/ isn't a package
and both scripts intentionally stay dependency-free of app/*).
"""

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "replay_detections", Path(__file__).resolve().parent.parent / "scripts" / "replay_detections.py"
)
assert _SPEC is not None and _SPEC.loader is not None
replay_detections = importlib.util.module_from_spec(_SPEC)
sys.modules["replay_detections"] = replay_detections
_SPEC.loader.exec_module(replay_detections)


def _det(sensor_id: str, ts: str, **extra) -> dict:
    return {
        "id": 1, "site_id": 1, "track_id": 2, "georeferenced": True, "human_label": "drone",
        "sensor_id": sensor_id, "sensor_type": "radar", "timestamp": ts,
        "latitude": 51.5, "longitude": -0.1, "confidence": 0.9, **extra,
    }


def test_strip_server_fields_removes_only_server_assigned_keys():
    stripped = replay_detections.strip_server_fields(_det("radar-1", "2026-01-01T00:00:00"))
    assert "id" not in stripped
    assert "track_id" not in stripped
    assert "site_id" not in stripped
    assert "georeferenced" not in stripped
    assert "human_label" not in stripped
    assert stripped["sensor_id"] == "radar-1"
    assert stripped["confidence"] == 0.9


def test_rewrite_timestamps_lands_the_first_detection_exactly_on_replay_start():
    detections = [_det("a", "2026-01-01T00:00:00"), _det("a", "2026-01-01T00:00:10")]
    replay_start = datetime(2026, 6, 1, 12, 0, 0)
    rewritten = replay_detections.rewrite_timestamps(detections, replay_start, speed=1.0)
    assert rewritten[0]["timestamp"] == replay_start.isoformat()


def test_rewrite_timestamps_preserves_relative_spacing_at_normal_speed():
    detections = [_det("a", "2026-01-01T00:00:00"), _det("a", "2026-01-01T00:00:10")]
    replay_start = datetime(2026, 6, 1, 12, 0, 0)
    rewritten = replay_detections.rewrite_timestamps(detections, replay_start, speed=1.0)
    gap = datetime.fromisoformat(rewritten[1]["timestamp"]) - datetime.fromisoformat(rewritten[0]["timestamp"])
    assert gap.total_seconds() == 10.0


def test_rewrite_timestamps_scales_spacing_by_speed():
    detections = [_det("a", "2026-01-01T00:00:00"), _det("a", "2026-01-01T00:00:10")]
    replay_start = datetime(2026, 6, 1, 12, 0, 0)
    rewritten = replay_detections.rewrite_timestamps(detections, replay_start, speed=2.0)
    gap = datetime.fromisoformat(rewritten[1]["timestamp"]) - datetime.fromisoformat(rewritten[0]["timestamp"])
    assert gap.total_seconds() == 5.0


def test_rewrite_timestamps_of_empty_list_is_empty():
    assert replay_detections.rewrite_timestamps([], datetime(2026, 1, 1), speed=1.0) == []


def test_compute_delays_first_detection_has_zero_delay():
    detections = [_det("a", "2026-01-01T00:00:00"), _det("a", "2026-01-01T00:00:05")]
    delays = replay_detections.compute_delays_s(detections, speed=1.0)
    assert delays[0] == 0.0


def test_compute_delays_matches_recorded_gap_at_normal_speed():
    detections = [_det("a", "2026-01-01T00:00:00"), _det("a", "2026-01-01T00:00:05")]
    delays = replay_detections.compute_delays_s(detections, speed=1.0)
    assert delays[1] == 5.0


def test_compute_delays_scales_by_speed():
    detections = [_det("a", "2026-01-01T00:00:00"), _det("a", "2026-01-01T00:00:08")]
    delays = replay_detections.compute_delays_s(detections, speed=4.0)
    assert delays[1] == 2.0


def test_compute_delays_never_negative_even_if_out_of_order():
    # Defensive: a source that somehow returned detections out of order
    # shouldn't produce a negative sleep (which time.sleep would reject).
    detections = [_det("a", "2026-01-01T00:00:10"), _det("a", "2026-01-01T00:00:00")]
    delays = replay_detections.compute_delays_s(detections, speed=1.0)
    assert delays[1] == 0.0


def test_save_then_load_recorded_round_trips(tmp_path):
    path = tmp_path / "captured.jsonl"
    detections = [_det("a", "2026-01-01T00:00:00"), _det("b", "2026-01-01T00:00:05")]
    replay_detections.save_recorded(str(path), detections)
    loaded = replay_detections.load_recorded(str(path))
    assert loaded == detections


def test_load_recorded_skips_blank_lines(tmp_path):
    path = tmp_path / "captured.jsonl"
    path.write_text(json.dumps(_det("a", "2026-01-01T00:00:00")) + "\n\n")
    assert len(replay_detections.load_recorded(str(path))) == 1
