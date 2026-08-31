"""app.adapters.rf_sweep_bridge's file-based logic (load_baseline_noise_floor_db)
-- watch() itself isn't unit tested here, same convention as this
package's other stdin/socket/scan-loop bridges (dji_droneid_bridge.py,
asterix_bridge.py, astm_remote_id_ble_bridge.py): it was instead manually
verified end to end by piping synthetic hackrf_sweep CSV through a real
running server (see this repo's session notes / PR description).
"""

import pytest

from app.adapters.rf_sweep_bridge import load_baseline_noise_floor_db

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
