from app.rf_signatures import match_rf_signature


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
