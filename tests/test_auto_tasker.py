"""samples/lattice/orbit_task/auto_tasker.py -- the COP-watching loop that
auto-creates an Orbit task for each new sensor point-of-interest entity,
tested against a fake Lattice client (matching the real SDK's method
signatures -- not a live Lattice environment, same limitation as every
other Lattice-dependent script in this repo).
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("anduril")

_ORBIT_DIR = Path(__file__).resolve().parent.parent / "samples" / "lattice" / "orbit_task"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _ORBIT_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("spec", "spec.py")
auto_tasker = _load("auto_tasker", "auto_tasker.py")


def _poi_entity(entity_id: str, lat: float | None = 1.0, lon: float | None = 2.0):
    # A real anduril.Entity, not a MagicMock -- _task_poi passes this
    # straight into TaskEntity(entity=...), and pydantic validates that
    # field's type strictly (a MagicMock fails validation even though the
    # real stream_entities() call always yields real Entity instances).
    import anduril

    location = None
    if lat is not None:
        location = anduril.Location(position=anduril.Position(latitude_degrees=lat, longitude_degrees=lon))
    return anduril.Entity(
        entity_id=entity_id,
        location=location,
        ontology=anduril.Ontology(template="TEMPLATE_SENSOR_POINT_OF_INTEREST"),
    )


def _non_poi_entity(entity_id: str):
    import anduril

    return anduril.Entity(entity_id=entity_id, ontology=anduril.Ontology(template="TEMPLATE_ASSET"))


def _event(event_type: str, entity, event="entity"):
    evt = MagicMock()
    evt.event = event
    evt.event_type = event_type
    evt.entity = entity
    return evt


def test_task_poi_skips_an_entity_with_no_position_yet():
    client = MagicMock()
    result = auto_tasker._task_poi(client, "orbit-asset-1", _poi_entity("poi-1", lat=None), 200.0, 100.0, 120.0)
    assert result is None
    client.tasks.create_task.assert_not_called()


def test_task_poi_creates_a_task_assigned_to_the_given_asset():
    client = MagicMock()
    auto_tasker._task_poi(client, "orbit-asset-1", _poi_entity("poi-1", lat=51.5, lon=-0.1), 200.0, 100.0, 120.0)

    kwargs = client.tasks.create_task.call_args.kwargs
    assert kwargs["relations"].assignee.system.entity_id == "orbit-asset-1"
    assert kwargs["specification"].latitude_degrees == 51.5
    assert kwargs["specification"].longitude_degrees == -0.1
    assert kwargs["specification"].radius_meters == 200.0
    assert kwargs["initial_entities"][0].entity.entity_id == "poi-1"


def test_watch_tasks_a_new_poi_exactly_once_even_if_it_recurs_in_the_stream(monkeypatch):
    task_mock = MagicMock()
    monkeypatch.setattr(auto_tasker, "_task_poi", task_mock)

    poi = _poi_entity("poi-1")
    client = MagicMock()
    client.entities.stream_entities.return_value = iter(
        [
            _event("EVENT_TYPE_CREATED", poi),
            _event("EVENT_TYPE_PREEXISTING", poi),  # same POI seen again (e.g. a stream reconnect)
        ]
    )

    auto_tasker.watch(client, "orbit-asset-1", 200.0, 100.0, 120.0)

    assert task_mock.call_count == 1


def test_watch_ignores_non_poi_entities():
    client = MagicMock()
    client.entities.stream_entities.return_value = iter([_event("EVENT_TYPE_CREATED", _non_poi_entity("asset-1"))])

    # Must not raise even with no _task_poi call expected.
    auto_tasker.watch(client, "orbit-asset-1", 200.0, 100.0, 120.0)
    client.tasks.create_task.assert_not_called()


def test_watch_re_tasks_a_poi_after_it_is_deleted_and_recreated(monkeypatch):
    task_mock = MagicMock()
    monkeypatch.setattr(auto_tasker, "_task_poi", task_mock)

    poi = _poi_entity("poi-1")
    client = MagicMock()
    client.entities.stream_entities.return_value = iter(
        [
            _event("EVENT_TYPE_CREATED", poi),
            _event("EVENT_TYPE_DELETED", poi),
            _event("EVENT_TYPE_CREATED", poi),
        ]
    )

    auto_tasker.watch(client, "orbit-asset-1", 200.0, 100.0, 120.0)

    assert task_mock.call_count == 2


def test_watch_ignores_heartbeats():
    client = MagicMock()
    heartbeat = MagicMock()
    heartbeat.event = "heartbeat"
    client.entities.stream_entities.return_value = iter([heartbeat])

    auto_tasker.watch(client, "orbit-asset-1", 200.0, 100.0, 120.0)  # must not raise
