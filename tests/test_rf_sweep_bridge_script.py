"""app.adapters.rf_sweep_bridge's file-based logic
(load_baseline_noise_floor_db) plus watch()'s own stdin-driven detection
loop -- unlike this package's socket/hardware-driven bridges (dji_droneid_
bridge.py, asterix_bridge.py, onvif_ptz_bridge.py), this one has no SDR/
hardware dependency at all (it's a plain CSV-lines stdin consumer, see the
module docstring), so its loop is exercised directly with a fake stdin
rather than only manually against real hardware.
"""

import argparse
import io
import urllib.error

import pytest

from app.adapters.rf_sweep_bridge import load_baseline_noise_floor_db, watch

_QUIET_LINE = "2026-01-01, 12:00:00.000000, 2400000000, 2400600000, 200000.0, 3, -70.0, -71.0, -69.0"
_NOISY_LINE = "2026-01-01, 12:00:00.100000, 2400600000, 2401200000, 200000.0, 2, -30.0, -25.0"


def test_load_baseline_noise_floor_db_computes_the_median_across_every_line(tmp_path):
    path = tmp_path / "control.csv"
    path.write_text(_QUIET_LINE + "\n")
    # powers: -70, -71, -69 -> sorted -71,-70,-69 -> median -70
    assert load_baseline_noise_floor_db(str(path)) == -70.0


def test_load_baseline_noise_floor_db_combines_multiple_lines(tmp_path):
    path = tmp_path / "control.csv"
    path.write_text(_QUIET_LINE + "\n" + _NOISY_LINE + "\n")
    # powers: -70,-71,-69,-30,-25 -> sorted -71,-70,-69,-30,-25 -> median -69
    assert load_baseline_noise_floor_db(str(path)) == -69.0


def test_load_baseline_noise_floor_db_skips_unparseable_lines(tmp_path):
    path = tmp_path / "control.csv"
    path.write_text("garbage,line\n" + _QUIET_LINE + "\n")
    assert load_baseline_noise_floor_db(str(path)) == -70.0


def test_load_baseline_noise_floor_db_rejects_a_file_with_no_valid_lines(tmp_path):
    path = tmp_path / "control.csv"
    path.write_text("garbage,line\nalso,garbage\n")
    with pytest.raises(SystemExit, match="no parseable"):
        load_baseline_noise_floor_db(str(path))


# A single sweep line: powers -70, -71, -20 -- median (own noise floor,
# no --baseline-csv) is -70, so the -20 bin is +50dB over it, well past
# the default 15dB threshold.
_ONE_PEAK_LINE = "2026-01-01, 12:00:00.000000, 2400000000, 2400600000, 200000.0, 3, -70.0, -71.0, -20.0"


def _bridge_args(**overrides):
    defaults = {
        "sensor_id": "rf-sweep-1", "api_url": "http://127.0.0.1:8000/api/detections", "api_key": "",
        "max_retries": 0, "retry_backoff": 1.0, "target_lat": 51.5, "target_lon": -0.1, "threshold_db": 15.0,
        "baseline_csv": None, "confidence": 0.4, "min_interval": 30.0, "min_interval_bucket_hz": 5_000_000.0,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_watch_posts_a_detection_for_a_bin_above_the_noise_floor(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(_ONE_PEAK_LINE + "\n"))
    posted = []
    monkeypatch.setattr(
        "app.adapters.rf_sweep_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    watch(_bridge_args())

    assert len(posted) == 1
    assert posted[0]["confidence"] == 0.4
    assert posted[0]["latitude"] == 51.5
    assert posted[0]["longitude"] == -0.1


def test_watch_reports_no_detection_when_nothing_exceeds_the_threshold(monkeypatch):
    quiet_line = "2026-01-01, 12:00:00.000000, 2400000000, 2400600000, 200000.0, 3, -70.0, -71.0, -69.0"
    monkeypatch.setattr("sys.stdin", io.StringIO(quiet_line + "\n"))
    posted = []
    monkeypatch.setattr(
        "app.adapters.rf_sweep_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload),
    )

    watch(_bridge_args())

    assert posted == []


def test_watch_deduplicates_repeated_detections_in_the_same_frequency_bucket(monkeypatch):
    # Two separate sweep cycles (different timestamps), both flagging the
    # same bin -- within --min-interval, the second should be suppressed.
    line1 = "2026-01-01, 12:00:00.000000, 2400000000, 2400600000, 200000.0, 3, -70.0, -71.0, -20.0"
    line2 = "2026-01-01, 12:00:01.000000, 2400000000, 2400600000, 200000.0, 3, -70.0, -71.0, -20.0"
    monkeypatch.setattr("sys.stdin", io.StringIO(line1 + "\n" + line2 + "\n"))
    posted = []
    monkeypatch.setattr(
        "app.adapters.rf_sweep_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )
    # Explicit, deterministic clock -- time.monotonic()'s absolute value is
    # whatever this machine's own uptime happens to be, not guaranteed
    # large enough on its own to clear a big --min-interval on the very
    # first (never-yet-posted) bucket.
    clock = iter([20000.0, 20001.0])
    monkeypatch.setattr("app.adapters.rf_sweep_bridge.time.monotonic", lambda: next(clock))

    watch(_bridge_args(min_interval=9999.0))

    assert len(posted) == 1


def test_watch_uses_a_fixed_baseline_noise_floor_when_configured(monkeypatch, tmp_path):
    baseline_path = tmp_path / "control.csv"
    baseline_path.write_text("2026-01-01, 11:00:00.000000, 2400000000, 2400600000, 200000.0, 3, -70.0, -71.0, -69.0\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(_ONE_PEAK_LINE + "\n"))
    posted = []
    monkeypatch.setattr(
        "app.adapters.rf_sweep_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    watch(_bridge_args(baseline_csv=str(baseline_path)))

    assert len(posted) == 1
    assert posted[0]["raw_data"]["noise_floor_db"] == -70.0


def test_watch_survives_a_post_failure(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(_ONE_PEAK_LINE + "\n"))

    def failing_post(url, payload, api_key, max_retries, retry_backoff_s):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.adapters.rf_sweep_bridge.post_detection", failing_post)

    watch(_bridge_args())

    assert "ERROR posting detection" in capsys.readouterr().out


def test_watch_skips_malformed_lines_without_crashing(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("garbage,line\n" + _ONE_PEAK_LINE + "\n"))
    posted = []
    monkeypatch.setattr(
        "app.adapters.rf_sweep_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )

    watch(_bridge_args())

    assert len(posted) == 1
