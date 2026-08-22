"""Builds and parses the custom `Orbit` task specification defined in
orbit.proto -- see that file's header comment for why this doesn't need
protoc/compiled bindings: Lattice's REST/JSON transport represents
google.protobuf.Any as a plain {"@type": ..., ...fields} object, which
this module produces/consumes directly as plain dicts.

Kept dependency-free (no `anduril` import), same split as
app/adapters/lattice.py vs lattice_bridge.py in the main app:
publish_poi.py/orbit_asset.py/auto_tasker.py import `anduril` lazily and
wrap this module's plain dicts in `GoogleProtobufAny(**spec)`.
"""

from __future__ import annotations

ORBIT_TASK_TYPE = "type.googleapis.com/anduril.tasksample.v1.Orbit"

_REQUIRED_FIELDS = ("latitude_degrees", "longitude_degrees", "radius_meters")


def build_orbit_specification(
    latitude_degrees: float,
    longitude_degrees: float,
    radius_meters: float,
    altitude_meters: float = 100.0,
    duration_seconds: float = 300.0,
) -> dict:
    """Returns the kwargs for `GoogleProtobufAny(**spec)` -- `@type` is
    passed as `type` (GoogleProtobufAny's Python field name; the SDK
    serializes it back to the `@type` JSON key on the wire, matching
    protobuf's standard JSON mapping for Any -- see this SDK's pydantic
    model config, which sets alias='@type').
    """
    return {
        "type": ORBIT_TASK_TYPE,
        "latitude_degrees": latitude_degrees,
        "longitude_degrees": longitude_degrees,
        "radius_meters": radius_meters,
        "altitude_meters": altitude_meters,
        "duration_seconds": duration_seconds,
    }


def parse_orbit_specification(payload: dict) -> dict:
    """The reverse direction: given a task specification as received by
    an agent (an Orbit task's `task.specification.model_dump(by_alias=True)`,
    keyed by `@type`), validates it's actually an Orbit task and returns
    the plain parameter dict `orbit_asset.py` needs to run the simulation.
    Raises ValueError for a specification that isn't an Orbit task at all,
    or an Orbit task missing a required field -- either means the caller
    (an agent's stream_as_agent loop) has no business trying to execute
    this as an orbit.
    """
    actual_type = payload.get("@type") or payload.get("type")
    if actual_type != ORBIT_TASK_TYPE:
        raise ValueError(f"Not an Orbit task specification (type={actual_type!r})")

    missing = [f for f in _REQUIRED_FIELDS if payload.get(f) is None]
    if missing:
        raise ValueError(f"Orbit specification missing required field(s): {', '.join(missing)}")

    return {
        "latitude_degrees": float(payload["latitude_degrees"]),
        "longitude_degrees": float(payload["longitude_degrees"]),
        "radius_meters": float(payload["radius_meters"]),
        "altitude_meters": float(payload.get("altitude_meters", 100.0)),
        "duration_seconds": float(payload.get("duration_seconds", 300.0)),
    }
