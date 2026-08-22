"""samples/lattice/entity_visualizer/ -- entities.py's pure GeoJSON mapping
(dependency-free, unit-tested directly) and server.py's HTTP handler +
streaming loop (tested against a fake Lattice client, not a live one --
same limitation as every other Lattice-dependent script in this repo).
"""

import importlib.util
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

_VISUALIZER_DIR = Path(__file__).resolve().parent.parent / "samples" / "lattice" / "entity_visualizer"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _VISUALIZER_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


entities_mod = _load("entities", "entities.py")


def test_entity_with_no_position_is_skipped():
    assert entities_mod.entity_to_geojson_feature({"entityId": "x", "aliases": {"name": "X"}}) is None


def test_entity_with_a_position_becomes_a_point_feature():
    entity = {
        "entityId": "drone-multi-sensor-track-1",
        "isLive": True,
        "location": {"position": {"latitudeDegrees": 51.5, "longitudeDegrees": -0.1, "altitudeHaeMeters": 120.0}},
        "aliases": {"name": "Track 1"},
        "milView": {"disposition": "DISPOSITION_SUSPICIOUS", "environment": "ENVIRONMENT_AIR"},
        "ontology": {"template": "TEMPLATE_TRACK"},
    }
    feature = entities_mod.entity_to_geojson_feature(entity)

    assert feature["type"] == "Feature"
    # GeoJSON is [lon, lat, alt] -- the opposite axis order from the
    # Lattice field names, deliberately checked here since it's an easy
    # bug to introduce silently.
    assert feature["geometry"]["coordinates"] == [-0.1, 51.5, 120.0]
    assert feature["properties"]["entity_id"] == "drone-multi-sensor-track-1"
    assert feature["properties"]["name"] == "Track 1"
    assert feature["properties"]["disposition"] == "DISPOSITION_SUSPICIOUS"


def test_entity_without_altitude_omits_the_third_coordinate():
    entity = {
        "entityId": "x",
        "location": {"position": {"latitudeDegrees": 1.0, "longitudeDegrees": 2.0}},
    }
    feature = entities_mod.entity_to_geojson_feature(entity)
    assert feature["geometry"]["coordinates"] == [2.0, 1.0]


def test_entity_with_no_alias_falls_back_to_entity_id_as_the_display_name():
    entity = {"entityId": "x-1", "location": {"position": {"latitudeDegrees": 1.0, "longitudeDegrees": 2.0}}}
    feature = entities_mod.entity_to_geojson_feature(entity)
    assert feature["properties"]["name"] == "x-1"


def test_entities_to_feature_collection_skips_positionless_entities():
    entities = {
        "a": {"entityId": "a", "location": {"position": {"latitudeDegrees": 1.0, "longitudeDegrees": 2.0}}},
        "b": {"entityId": "b"},  # no location at all
    }
    collection = entities_mod.entities_to_feature_collection(entities)
    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 1
    assert collection["features"][0]["properties"]["entity_id"] == "a"


# --- server.py: streaming loop + HTTP handler, against a fake client ---


class _FakeEvent:
    def __init__(self, event, event_type=None, entity=None):
        self.event = event
        self.event_type = event_type
        self.entity = entity


class _FakeEntity:
    def __init__(self, entity_id, lat, lon):
        self._dump = {
            "entityId": entity_id,
            "isLive": True,
            "location": {"position": {"latitudeDegrees": lat, "longitudeDegrees": lon}},
        }

    def model_dump(self, by_alias=True, exclude_none=True):
        return self._dump


class _FakeEntitiesClient:
    """Yields a fixed sequence of stream events once, then blocks forever
    -- simulates a real (long-lived) stream_entities() call without
    actually needing network I/O or a live Lattice environment.
    """

    def __init__(self, events):
        self._events = events

    def stream_entities(self, **kwargs):
        yield from self._events
        threading.Event().wait()  # block "forever", like a real open stream


class _FakeClient:
    def __init__(self, events):
        self.entities = _FakeEntitiesClient(events)


@pytest.fixture
def server_mod(monkeypatch):
    module = _load("server", "server.py")
    monkeypatch.setattr(module, "_entities", {})
    return module


def test_stream_loop_populates_the_cache_from_created_and_update_events(server_mod):
    events = [
        _FakeEvent("entity", "EVENT_TYPE_CREATED", _FakeEntity("a", 1.0, 2.0)),
        _FakeEvent("entity", "EVENT_TYPE_UPDATE", _FakeEntity("b", 3.0, 4.0)),
        _FakeEvent("heartbeat"),
    ]
    thread = threading.Thread(
        target=server_mod._stream_loop, args=(_FakeClient(events), 5.0), daemon=True
    )
    thread.start()
    thread.join(timeout=2)  # the fake client blocks after its events, so this always hits the timeout

    assert set(server_mod._entities.keys()) == {"a", "b"}


def test_stream_loop_removes_deleted_entities_from_the_cache(server_mod):
    events = [
        _FakeEvent("entity", "EVENT_TYPE_CREATED", _FakeEntity("a", 1.0, 2.0)),
        _FakeEvent("entity", "EVENT_TYPE_DELETED", _FakeEntity("a", 1.0, 2.0)),
    ]
    thread = threading.Thread(
        target=server_mod._stream_loop, args=(_FakeClient(events), 5.0), daemon=True
    )
    thread.start()
    thread.join(timeout=2)

    assert server_mod._entities == {}


def test_http_server_serves_entities_as_a_feature_collection(server_mod):
    server_mod._entities["a"] = {
        "entityId": "a",
        "location": {"position": {"latitudeDegrees": 1.0, "longitudeDegrees": 2.0}},
    }
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", httpd.server_port)
        conn.request("GET", "/api/entities")
        response = conn.getresponse()
        assert response.status == 200
        import json

        body = json.loads(response.read())
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
    finally:
        httpd.shutdown()


def test_http_server_serves_the_static_index_page(server_mod):
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", httpd.server_port)
        conn.request("GET", "/")
        response = conn.getresponse()
        assert response.status == 200
        assert b"Lattice Entity Visualizer" in response.read()
    finally:
        httpd.shutdown()


def test_http_server_refuses_path_traversal_outside_the_static_dir(server_mod):
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", httpd.server_port)
        conn.request("GET", "/../server.py")
        response = conn.getresponse()
        response.read()
        assert response.status == 404
    finally:
        httpd.shutdown()
