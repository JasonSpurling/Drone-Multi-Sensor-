"""samples/lattice/orbit_task/spec.py -- builds/parses the custom Orbit
task specification, dependency-free (see that module's docstring for why
no protoc/compiled bindings are needed).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC_PATH = Path(__file__).resolve().parent.parent / "samples" / "lattice" / "orbit_task" / "spec.py"
_MODULE_SPEC = importlib.util.spec_from_file_location("orbit_spec", _SPEC_PATH)
assert _MODULE_SPEC is not None and _MODULE_SPEC.loader is not None
orbit_spec = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules["orbit_spec"] = orbit_spec
_MODULE_SPEC.loader.exec_module(orbit_spec)


def test_build_orbit_specification_sets_the_type_and_all_fields():
    spec = orbit_spec.build_orbit_specification(
        latitude_degrees=51.5, longitude_degrees=-0.1, radius_meters=250.0
    )
    assert spec["type"] == orbit_spec.ORBIT_TASK_TYPE
    assert spec["latitude_degrees"] == 51.5
    assert spec["longitude_degrees"] == -0.1
    assert spec["radius_meters"] == 250.0
    assert spec["altitude_meters"] == 100.0  # default
    assert spec["duration_seconds"] == 300.0  # default


def test_parse_orbit_specification_round_trips_build_output():
    built = orbit_spec.build_orbit_specification(
        latitude_degrees=1.0, longitude_degrees=2.0, radius_meters=300.0, altitude_meters=50.0, duration_seconds=60.0
    )
    # Simulates the wire round trip: build_orbit_specification's "type"
    # becomes the JSON "@type" key once through GoogleProtobufAny's alias.
    wire_payload = {**built, "@type": built.pop("type")}
    parsed = orbit_spec.parse_orbit_specification(wire_payload)

    assert parsed == {
        "latitude_degrees": 1.0,
        "longitude_degrees": 2.0,
        "radius_meters": 300.0,
        "altitude_meters": 50.0,
        "duration_seconds": 60.0,
    }


def test_parse_orbit_specification_rejects_a_different_task_type():
    with pytest.raises(ValueError, match="Not an Orbit task"):
        orbit_spec.parse_orbit_specification({"@type": "type.googleapis.com/some.other.Task"})


@pytest.mark.parametrize("missing_field", ["latitude_degrees", "longitude_degrees", "radius_meters"])
def test_parse_orbit_specification_rejects_a_missing_required_field(missing_field):
    payload = {
        "@type": orbit_spec.ORBIT_TASK_TYPE,
        "latitude_degrees": 1.0,
        "longitude_degrees": 2.0,
        "radius_meters": 3.0,
    }
    del payload[missing_field]
    with pytest.raises(ValueError, match=missing_field):
        orbit_spec.parse_orbit_specification(payload)


def test_parse_orbit_specification_fills_in_optional_field_defaults():
    payload = {
        "@type": orbit_spec.ORBIT_TASK_TYPE,
        "latitude_degrees": 1.0,
        "longitude_degrees": 2.0,
        "radius_meters": 3.0,
    }
    parsed = orbit_spec.parse_orbit_specification(payload)
    assert parsed["altitude_meters"] == 100.0
    assert parsed["duration_seconds"] == 300.0
