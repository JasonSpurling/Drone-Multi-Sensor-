import pytest

from app.adapters.sbs1 import parse_sbs1_line

AIRBORNE_POSITION_LINE = (
    "MSG,3,1,1,4CA593,1,2026/08/18,12:34:56.789,2026/08/18,12:34:56.789,,"
    "38000,,,51.4700,-0.4543,,,,,,0"
)
IDENTIFICATION_LINE = (
    "MSG,1,1,1,4CA593,1,2026/08/18,12:34:56.789,2026/08/18,12:34:56.789,"
    "RYR123,,,,,,,,,,,"
)


def test_parses_airborne_position_message():
    payload = parse_sbs1_line(AIRBORNE_POSITION_LINE, sensor_id="dump1090-test")
    assert payload is not None
    assert payload["sensor_id"] == "dump1090-test"
    assert payload["sensor_type"] == "adsb"
    assert payload["latitude"] == 51.4700
    assert payload["longitude"] == -0.4543
    assert payload["raw_data"]["hex_ident"] == "4CA593"


def test_converts_altitude_feet_to_meters():
    payload = parse_sbs1_line(AIRBORNE_POSITION_LINE)
    assert payload["altitude_m"] == pytest.approx(38000 * 0.3048)


def test_ignores_non_position_message_types():
    assert parse_sbs1_line(IDENTIFICATION_LINE) is None


def test_ignores_malformed_lines():
    assert parse_sbs1_line("not,a,valid,sbs,line") is None
    assert parse_sbs1_line("") is None


def test_ignores_position_message_missing_coordinates():
    line = (
        "MSG,3,1,1,4CA593,1,2026/08/18,12:34:56.789,2026/08/18,12:34:56.789,,"
        "38000,,,,,,,,,,0"
    )
    assert parse_sbs1_line(line) is None
