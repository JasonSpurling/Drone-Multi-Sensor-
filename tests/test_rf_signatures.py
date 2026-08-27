import json

from app.rf_signatures import BUILT_IN_SIGNATURES, RfSignature, load_operator_signatures, match_rf_signature


def test_dji_ocusync_band_matches():
    result = match_rf_signature(center_frequency_mhz=2440.0, bandwidth_mhz=10.0, frequency_hopping=True)
    assert result.signature is not None
    assert result.signature.name == "dji_ocusync"
    assert result.confidence > 0.8


def test_dji_ocusync_5_8ghz_band_matches():
    result = match_rf_signature(center_frequency_mhz=5780.0, bandwidth_mhz=10.0, frequency_hopping=True)
    assert result.signature is not None
    assert result.signature.name == "dji_ocusync"


def test_analog_fpv_matches_wide_bandwidth_fixed_frequency():
    result = match_rf_signature(center_frequency_mhz=5800.0, bandwidth_mhz=20.0, frequency_hopping=False)
    assert result.signature is not None
    assert result.signature.name == "analog_fpv"


def test_no_match_outside_any_known_band():
    result = match_rf_signature(center_frequency_mhz=900.0, bandwidth_mhz=10.0)
    assert result.signature is None
    assert result.confidence == 0.0


def test_no_match_for_bandwidth_outside_signature_window():
    # In-band but far too narrow for any known signature.
    result = match_rf_signature(center_frequency_mhz=2440.0, bandwidth_mhz=0.5)
    assert result.signature is None


def test_missing_frequency_or_bandwidth_does_not_match():
    assert match_rf_signature(center_frequency_mhz=None, bandwidth_mhz=10.0).signature is None
    assert match_rf_signature(center_frequency_mhz=2440.0, bandwidth_mhz=None).signature is None


def test_hopping_mismatch_excludes_a_hopping_specific_signature():
    # DJI OcuSync requires frequency_hopping=True; explicitly non-hopping
    # in the same band/bandwidth window must not match it.
    result = match_rf_signature(center_frequency_mhz=2440.0, bandwidth_mhz=10.0, frequency_hopping=False)
    assert result.signature is None or result.signature.name != "dji_ocusync"


def test_unspecified_hopping_still_allows_a_match():
    result = match_rf_signature(center_frequency_mhz=2440.0, bandwidth_mhz=10.0, frequency_hopping=None)
    assert result.signature is not None


def test_load_operator_signatures_returns_empty_when_path_is_none():
    assert load_operator_signatures(None) == ()


def test_load_operator_signatures_warns_and_returns_empty_for_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    assert load_operator_signatures(missing) == ()


def test_load_operator_signatures_parses_a_real_file(tmp_path):
    path = tmp_path / "signatures.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "test_model",
                    "label": "Test Model X1",
                    "freq_bands_mhz": [[2400.0, 2483.5]],
                    "bandwidth_mhz": [9.0, 11.0],
                    "frequency_hopping": True,
                    "drone_link_confidence": 0.99,
                    "source": "field capture, 2026-01-01, verified against known unit",
                }
            ]
        )
    )
    signatures = load_operator_signatures(path)
    assert len(signatures) == 1
    assert signatures[0].name == "test_model"
    assert signatures[0].source == "field capture, 2026-01-01, verified against known unit"


def test_load_operator_signatures_defaults_source_when_omitted(tmp_path):
    path = tmp_path / "signatures.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "test_model",
                    "label": "Test Model X1",
                    "freq_bands_mhz": [[2400.0, 2483.5]],
                    "bandwidth_mhz": [9.0, 11.0],
                    "frequency_hopping": True,
                    "drone_link_confidence": 0.99,
                }
            ]
        )
    )
    signatures = load_operator_signatures(path)
    assert "operator-supplied" in signatures[0].source


def test_operator_signature_is_tried_before_built_ins():
    # A verified per-model operator entry should win over the approximate
    # family-level fallback for the same frequency/bandwidth window.
    operator_signature = RfSignature(
        name="verified_model",
        label="Verified Model",
        freq_bands_mhz=((2400.0, 2483.5),),
        bandwidth_mhz=(8.0, 20.0),
        frequency_hopping=True,
        drone_link_confidence=0.99,
        source="field capture",
    )
    result = match_rf_signature(
        center_frequency_mhz=2440.0, bandwidth_mhz=10.0, frequency_hopping=True,
        signatures=(operator_signature, *BUILT_IN_SIGNATURES),
    )
    assert result.signature is not None
    assert result.signature.name == "verified_model"
    assert result.confidence == 0.99


def test_default_signatures_still_match_built_ins_when_no_operator_file_configured():
    # With no DRONE_RF_SIGNATURES_PATH set, match_rf_signature's default
    # (signatures=None) path must behave exactly like passing
    # BUILT_IN_SIGNATURES directly -- no silent behavior change for the
    # common case of not configuring operator signatures at all.
    result = match_rf_signature(center_frequency_mhz=2440.0, bandwidth_mhz=10.0, frequency_hopping=True)
    assert result.signature is not None
    assert result.signature.name == "dji_ocusync"
