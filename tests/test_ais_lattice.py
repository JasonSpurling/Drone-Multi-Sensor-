"""samples/lattice/ais/ais.py -- pure AIS-record-to-Lattice-Entity mapping,
kept dependency-free (see that module's docstring) so it's testable
without installing anduril-lattice-sdk, the same split as
tests/test_lattice_adapter.py for the main app's own Lattice integration.
"""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

_AIS_DIR = Path(__file__).resolve().parent.parent / "samples" / "lattice" / "ais"
_SPEC = importlib.util.spec_from_file_location("ais", _AIS_DIR / "ais.py")
assert _SPEC is not None and _SPEC.loader is not None
ais = importlib.util.module_from_spec(_SPEC)
sys.modules["ais"] = ais
_SPEC.loader.exec_module(ais)

import pytest  # noqa: E402


def test_parse_ais_record_normalizes_types():
    raw = {
        "mmsi": 366990001,
        "vessel_name": "MV SAMPLE",
        "ship_type": "cargo",
        "latitude": "37.81",
        "longitude": "-122.42",
        "sog_knots": "11.2",
        "cog_degrees": "265",
        "timestamp": "2026-08-22T06:00:00",
    }
    parsed = ais.parse_ais_record(raw)
    assert parsed["mmsi"] == "366990001"
    assert parsed["latitude"] == 37.81
    assert parsed["longitude"] == -122.42
    assert parsed["sog_knots"] == 11.2
    assert parsed["cog_degrees"] == 265.0


def test_parse_ais_record_allows_missing_optional_fields():
    parsed = ais.parse_ais_record({"mmsi": "1", "latitude": 1.0, "longitude": 2.0})
    assert parsed["vessel_name"] is None
    assert parsed["sog_knots"] is None
    assert parsed["cog_degrees"] is None


@pytest.mark.parametrize("missing_field", ["mmsi", "latitude", "longitude"])
def test_parse_ais_record_rejects_a_record_missing_a_required_field(missing_field):
    raw = {"mmsi": "1", "latitude": 1.0, "longitude": 2.0}
    del raw[missing_field]
    with pytest.raises(ValueError, match=missing_field):
        ais.parse_ais_record(raw)


def test_publish_entity_kwargs_maps_position_and_speed():
    record = ais.parse_ais_record(
        {"mmsi": "366990001", "vessel_name": "MV SAMPLE", "latitude": 37.81, "longitude": -122.42, "sog_knots": 10.0}
    )
    now = datetime(2026, 1, 1, 12, 0, 0)
    kwargs = ais.ais_record_to_publish_entity_kwargs(record, now=now)

    assert kwargs["entity_id"] == "ais-vessel-366990001"
    assert kwargs["aliases"]["name"] == "MV SAMPLE"
    assert kwargs["location"]["position"]["latitude_degrees"] == 37.81
    assert kwargs["location"]["position"]["longitude_degrees"] == -122.42
    assert kwargs["location"]["speed_mps"] == pytest.approx(10.0 * 0.514444)
    assert kwargs["mil_view"]["environment"] == "ENVIRONMENT_SURFACE"
    assert kwargs["mil_view"]["disposition"] == "DISPOSITION_NEUTRAL"
    assert kwargs["ontology"]["template"] == "TEMPLATE_TRACK"
    assert kwargs["expiry_time"] == now.__class__(2026, 1, 1, 12, 3, 0)


def test_publish_entity_kwargs_falls_back_to_mmsi_when_no_vessel_name():
    record = ais.parse_ais_record({"mmsi": "999", "latitude": 1.0, "longitude": 2.0})
    kwargs = ais.ais_record_to_publish_entity_kwargs(record)
    assert kwargs["aliases"]["name"] == "Vessel 999"


def test_publish_entity_kwargs_omits_speed_when_sog_is_absent():
    record = ais.parse_ais_record({"mmsi": "999", "latitude": 1.0, "longitude": 2.0})
    kwargs = ais.ais_record_to_publish_entity_kwargs(record)
    assert "speed_mps" not in kwargs["location"]
