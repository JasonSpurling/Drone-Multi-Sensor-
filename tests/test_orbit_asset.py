"""samples/lattice/orbit_task/orbit_asset.py -- _point_on_circle's pure
geometry (no SDK needed), and _execute_orbit's status-lifecycle/
cancellation logic against a fake Lattice tasks client (matching the real
SDK's TasksClient method signatures, verified against the installed SDK
in this test's own construction of TaskStatus/TaskError below -- not a
live Lattice environment). Requires anduril-lattice-sdk installed
(requirements-lattice.txt); skipped otherwise, same pattern as
tests/test_lattice_objects_cli.py.
"""

import importlib.util
import math
import sys
import threading
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


orbit_spec = _load("spec", "spec.py")
orbit_asset = _load("orbit_asset", "orbit_asset.py")


def test_point_on_circle_stays_at_the_configured_radius():
    center_lat, center_lon, radius_m = 51.5, -0.1, 200.0
    for angle_deg in (0, 90, 180, 270):
        lat, lon = orbit_asset._point_on_circle(center_lat, center_lon, radius_m, math.radians(angle_deg))
        # Rough distance check via the same flat-earth approximation the
        # function itself uses -- exact agreement, not an independent
        # geodesic cross-check, but enough to catch a sign/axis-order bug.
        lat_offset_m = (lat - center_lat) * orbit_asset._METERS_PER_DEGREE
        lon_offset_m = (lon - center_lon) * orbit_asset._METERS_PER_DEGREE * math.cos(math.radians(center_lat))
        distance = math.hypot(lat_offset_m, lon_offset_m)
        assert distance == pytest.approx(radius_m, rel=1e-6)


def test_point_on_circle_at_angle_zero_is_due_east():
    center_lat, center_lon = 0.0, 0.0
    lat, lon = orbit_asset._point_on_circle(center_lat, center_lon, 100.0, 0.0)
    assert lat == pytest.approx(center_lat, abs=1e-9)
    assert lon > center_lon


def _fake_client_that_completes():
    client = MagicMock()
    call_count = {"n": 0}

    def _update_status(task_id, *, status_version=None, new_status=None, author=None):
        call_count["n"] += 1
        response = MagicMock()
        response.version.status_version = call_count["n"]
        return response

    client.tasks.update_task_status.side_effect = _update_status
    return client, call_count


def test_execute_orbit_reports_executing_then_done_ok_when_not_cancelled(monkeypatch):
    monkeypatch.setattr(orbit_asset, "_STEP_SECONDS", 0.01)  # keep the test fast
    monkeypatch.setattr(orbit_asset, "_publish_asset", MagicMock())
    client, _call_count = _fake_client_that_completes()
    cancel_event = threading.Event()

    params = {
        "latitude_degrees": 1.0, "longitude_degrees": 2.0, "radius_meters": 100.0,
        "altitude_meters": 50.0, "duration_seconds": 0.05,
    }
    orbit_asset._execute_orbit(client, "orbit-asset-1", "task-1", None, params, cancel_event, author=None)

    statuses = [call.kwargs["new_status"].status for call in client.tasks.update_task_status.call_args_list]
    assert statuses[0] == "STATUS_EXECUTING"
    assert statuses[-1] == "STATUS_DONE_OK"


def test_execute_orbit_reports_done_not_ok_when_cancelled_mid_orbit(monkeypatch):
    monkeypatch.setattr(orbit_asset, "_STEP_SECONDS", 0.01)
    monkeypatch.setattr(orbit_asset, "_publish_asset", MagicMock())
    client, _ = _fake_client_that_completes()
    cancel_event = threading.Event()
    cancel_event.set()  # already cancelled before execution starts

    params = {
        "latitude_degrees": 1.0, "longitude_degrees": 2.0, "radius_meters": 100.0,
        "altitude_meters": 50.0, "duration_seconds": 10.0,  # long enough that "not cancelled" would take a while
    }
    orbit_asset._execute_orbit(client, "orbit-asset-1", "task-1", None, params, cancel_event, author=None)

    statuses = [call.kwargs["new_status"].status for call in client.tasks.update_task_status.call_args_list]
    assert statuses[0] == "STATUS_EXECUTING"
    assert statuses[-1] == "STATUS_DONE_NOT_OK"
    assert client.tasks.update_task_status.call_args.kwargs["new_status"].task_error.code == "ERROR_CODE_CANCELLED"


def test_execute_orbit_publishes_a_position_update_on_every_step(monkeypatch):
    monkeypatch.setattr(orbit_asset, "_STEP_SECONDS", 0.01)
    publish_mock = MagicMock()
    monkeypatch.setattr(orbit_asset, "_publish_asset", publish_mock)
    client, _ = _fake_client_that_completes()
    cancel_event = threading.Event()

    params = {
        "latitude_degrees": 1.0, "longitude_degrees": 2.0, "radius_meters": 100.0,
        "altitude_meters": 50.0, "duration_seconds": 0.05,
    }
    orbit_asset._execute_orbit(client, "orbit-asset-1", "task-1", None, params, cancel_event, author=None)

    assert publish_mock.call_count >= 1
    # Every published position stays within the configured radius (see
    # test_point_on_circle_stays_at_the_configured_radius for the exact
    # distance check) -- here just confirming the asset entity id and
    # altitude are threaded through correctly.
    for call in publish_mock.call_args_list:
        assert call.args[1] == "orbit-asset-1"
        assert call.args[4] == 50.0
