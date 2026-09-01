from app.adapters.rf_sweep import (
    SweepSegment,
    bins_from_segment,
    build_detection_payload,
    estimate_noise_floor_db,
    find_peaks,
    parse_sweep_line,
)

# A real (representative) hackrf_sweep CSV line: date, time, hz_low, hz_high,
# hz_bin_width, num_samples, then num_samples dB power values.
_LINE = "2026-01-01, 12:00:00.000000, 2400000000, 2400600000, 200000.0, 3, -70.0, -68.5, -30.0"


def test_parse_sweep_line_extracts_all_fields():
    segment = parse_sweep_line(_LINE)
    assert segment == SweepSegment(
        timestamp_key=("2026-01-01", "12:00:00.000000"),
        hz_low=2400000000,
        hz_bin_width=200000.0,
        powers_db=[-70.0, -68.5, -30.0],
    )


def test_parse_sweep_line_rejects_blank_lines():
    assert parse_sweep_line("") is None
    assert parse_sweep_line("\n") is None


def test_parse_sweep_line_rejects_too_few_fields():
    assert parse_sweep_line("2026-01-01, 12:00:00, 2400000000") is None


def test_parse_sweep_line_rejects_non_numeric_fields():
    assert parse_sweep_line("2026-01-01, 12:00:00, not_a_number, 2400600000, 200000.0, 1, -70.0") is None


def test_parse_sweep_line_rejects_a_power_count_mismatch():
    # num_samples says 3 but only 2 dB values follow.
    assert parse_sweep_line("2026-01-01, 12:00:00, 2400000000, 2400600000, 200000.0, 3, -70.0, -68.5") is None


def test_bins_from_segment_computes_bin_start_frequencies():
    segment = SweepSegment(
        timestamp_key=("d", "t"), hz_low=2400000000, hz_bin_width=200000.0, powers_db=[-70.0, -68.5, -30.0]
    )
    assert bins_from_segment(segment) == [
        (2400000000.0, -70.0),
        (2400200000.0, -68.5),
        (2400400000.0, -30.0),
    ]


def test_estimate_noise_floor_db_is_the_median():
    bins = [(1.0, -70.0), (2.0, -68.0), (3.0, -30.0), (4.0, -72.0)]
    # sorted: -72, -70, -68, -30 -> median of middle two = (-70 + -68) / 2
    assert estimate_noise_floor_db(bins) == -69.0


def test_estimate_noise_floor_db_of_empty_bins_is_zero():
    assert estimate_noise_floor_db([]) == 0.0


def test_find_peaks_flags_bins_above_threshold():
    bins = [(1.0, -70.0), (2.0, -68.0), (3.0, -30.0)]
    peaks = find_peaks(bins, noise_floor_db=-70.0, threshold_db=15.0)
    assert peaks == [(3.0, -30.0)]


def test_find_peaks_sorts_strongest_first():
    bins = [(1.0, -20.0), (2.0, -10.0), (3.0, -15.0)]
    peaks = find_peaks(bins, noise_floor_db=-70.0, threshold_db=15.0)
    assert [freq for freq, _ in peaks] == [2.0, 3.0, 1.0]


def test_find_peaks_empty_when_nothing_exceeds_threshold():
    bins = [(1.0, -70.0), (2.0, -71.0)]
    assert find_peaks(bins, noise_floor_db=-70.0, threshold_db=15.0) == []


def test_build_detection_payload_shape():
    payload = build_detection_payload(
        frequency_hz=2450000000.0, power_db=-30.0, noise_floor_db=-70.0,
        sensor_id="rf-sweep-1", target_lat=51.5, target_lon=-0.1, confidence=0.4,
    )
    assert payload["sensor_id"] == "rf-sweep-1"
    assert payload["sensor_type"] == "rf"
    assert payload["latitude"] == 51.5
    assert payload["longitude"] == -0.1
    assert payload["confidence"] == 0.4
    assert payload["raw_data"]["center_frequency_mhz"] == 2450.0
    assert payload["raw_data"]["peak_power_db"] == -30.0
    assert payload["raw_data"]["noise_floor_db"] == -70.0
    assert payload["raw_data"]["margin_db"] == 40.0
    assert "azimuth_deg" not in payload  # omnidirectional -- no bearing to report
    assert "timestamp" in payload
