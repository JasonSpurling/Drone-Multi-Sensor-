"""A taskable Lattice Asset that accepts and executes an Orbit task -- the
second of three programs in this directory demonstrating automated
tasking (see README.md).

Publishes itself as a taskable Entity (task_catalog advertises it accepts
Orbit tasks -- see spec.py/orbit.proto), then listens for tasks assigned
to it via the Tasks API's agent stream. On receiving an Orbit task,
*simulates* circling the specified point (there's no real vehicle behind
this script -- see this repo's README.md, "Downstream C2 integration",
for why the main drone tracker deliberately does NOT do this for a real
asset): republishes this entity's own position every couple of seconds
along the circle, reports STATUS_EXECUTING at the start and STATUS_DONE_OK
on completion (or STATUS_DONE_NOT_OK if cancelled mid-orbit), the real
task status lifecycle a live agent would report.

Usage:
    pip install -r requirements-lattice.txt
    export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
    export LATTICE_CLIENT_ID=...
    export LATTICE_CLIENT_SECRET=...
    export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes

    python samples/lattice/orbit_task/orbit_asset.py --asset-id orbit-asset-1 \\
        --start-lat 51.5 --start-lon -0.1
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta

from spec import ORBIT_TASK_TYPE, parse_orbit_specification

logger = logging.getLogger(__name__)

ASSET_EXPIRY_SECONDS = 60
# Simulated position update cadence while executing an orbit -- frequent
# enough to look like real movement on the entity_visualizer's map,
# infrequent enough not to spam Lattice with updates for a demo.
_STEP_SECONDS = 2.0
# Meters per degree of latitude, and (approximately, for the small radii
# an Orbit task realistically uses) of longitude at mid-latitudes -- a
# flat-earth approximation, not the real geodesic math app/geo.py uses in
# the main app. Fine at orbit radii of tens to low hundreds of meters;
# not accurate enough to reuse for anything at real geographic scale.
_METERS_PER_DEGREE = 111_320.0


def _point_on_circle(center_lat: float, center_lon: float, radius_m: float, angle_rad: float) -> tuple[float, float]:
    lat_offset_m = radius_m * math.sin(angle_rad)
    lon_offset_m = radius_m * math.cos(angle_rad)
    lat = center_lat + lat_offset_m / _METERS_PER_DEGREE
    lon = center_lon + lon_offset_m / (_METERS_PER_DEGREE * math.cos(math.radians(center_lat)))
    return lat, lon


def _publish_asset(client, asset_id: str, lat: float, lon: float, altitude_m: float, task_catalog) -> None:
    from anduril import Aliases, Location, MilView, Ontology, Position, Provenance

    client.entities.publish_entity(
        entity_id=asset_id,
        is_live=True,
        expiry_time=datetime.utcnow() + timedelta(seconds=ASSET_EXPIRY_SECONDS),
        aliases=Aliases(name=asset_id),
        location=Location(
            position=Position(latitude_degrees=lat, longitude_degrees=lon, altitude_hae_meters=altitude_m)
        ),
        mil_view=MilView(disposition="DISPOSITION_FRIENDLY", environment="ENVIRONMENT_AIR"),
        provenance=Provenance(integration_name="orbit-task-sample", data_type="orbit_asset", source_id=asset_id),
        ontology=Ontology(template="TEMPLATE_ASSET"),
        task_catalog=task_catalog,
    )


def _update_status(client, task_id: str, status_version: int | None, new_status, author) -> int | None:
    """Wraps update_task_status and returns the new status_version from
    its response, for the next call's optimistic-concurrency check --
    each status transition invalidates the version, so a fixed version
    reused across multiple calls would get every call after the first
    rejected (STATUS_VERSION_REJECTED).
    """
    updated = client.tasks.update_task_status(
        task_id, status_version=status_version, new_status=new_status, author=author
    )
    return updated.version.status_version if updated.version else None


def _execute_orbit(
    client,
    asset_id: str,
    task_id: str,
    status_version: int | None,
    params: dict,
    cancel_event: threading.Event,
    author,
) -> None:
    from anduril import TaskError, TaskStatus

    logger.info(
        "Task %s: starting orbit around (%s, %s)",
        task_id, params["latitude_degrees"], params["longitude_degrees"],
    )
    status_version = _update_status(client, task_id, status_version, TaskStatus(status="STATUS_EXECUTING"), author)

    steps = max(int(params["duration_seconds"] // _STEP_SECONDS), 1)
    for i in range(steps):
        if cancel_event.is_set():
            logger.info("Task %s: cancelled mid-orbit", task_id)
            _update_status(
                client,
                task_id,
                status_version,
                TaskStatus(
                    status="STATUS_DONE_NOT_OK",
                    task_error=TaskError(code="ERROR_CODE_CANCELLED", message="Orbit cancelled before completion"),
                ),
                author,
            )
            return
        angle = 2 * math.pi * (i / steps)
        lat, lon = _point_on_circle(
            params["latitude_degrees"], params["longitude_degrees"], params["radius_meters"], angle
        )
        _publish_asset(client, asset_id, lat, lon, params["altitude_meters"], task_catalog=None)
        time.sleep(_STEP_SECONDS)

    logger.info("Task %s: orbit complete", task_id)
    _update_status(client, task_id, status_version, TaskStatus(status="STATUS_DONE_OK"), author)


def run(client, asset_id: str, start_lat: float, start_lon: float) -> None:
    from anduril import EntityIdsSelector, Principal, System, TaskCatalog, TaskDefinition

    author = Principal(system=System(entity_id=asset_id, service_name="orbit-asset-sample"))
    task_catalog = TaskCatalog(task_definitions=[TaskDefinition(task_specification_url=ORBIT_TASK_TYPE)])

    _publish_asset(client, asset_id, start_lat, start_lon, altitude_m=100.0, task_catalog=task_catalog)
    print(f"Asset {asset_id} published at ({start_lat}, {start_lon}), listening for Orbit tasks...")

    cancel_events: dict[str, threading.Event] = {}
    for event in client.tasks.stream_as_agent(agent_selector=EntityIdsSelector(entity_ids=[asset_id])):
        if event.event != "agent_request":
            continue

        if event.execute_request is not None:
            task = event.execute_request.task
            if task is None or task.specification is None or task.version is None:
                continue
            task_id = task.version.task_id
            status_version = task.version.status_version
            try:
                params = parse_orbit_specification(task.specification.model_dump(by_alias=True))
            except ValueError as exc:
                logger.warning("Rejecting task %s: %s", task_id, exc)
                continue
            cancel_event = threading.Event()
            cancel_events[task_id] = cancel_event
            threading.Thread(
                target=_execute_orbit,
                args=(client, asset_id, task_id, status_version, params, cancel_event, author),
                daemon=True,
            ).start()

        elif event.cancel_request is not None:
            task_id = event.cancel_request.task_id
            cancel_event = cancel_events.get(task_id)
            if cancel_event is not None:
                cancel_event.set()

        elif event.complete_request is not None:
            task_id = event.complete_request.task_id
            cancel_event = cancel_events.get(task_id)
            if cancel_event is not None:
                cancel_event.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--asset-id", default="orbit-asset-1")
    parser.add_argument("--start-lat", type=float, required=True)
    parser.add_argument("--start-lon", type=float, required=True)
    parser.add_argument(
        "--lattice-endpoint", default=os.getenv("LATTICE_ENDPOINT"),
        required=not os.getenv("LATTICE_ENDPOINT"),
    )
    parser.add_argument(
        "--lattice-client-id", default=os.getenv("LATTICE_CLIENT_ID"),
        required=not os.getenv("LATTICE_CLIENT_ID"),
    )
    parser.add_argument(
        "--lattice-client-secret", default=os.getenv("LATTICE_CLIENT_SECRET"),
        required=not os.getenv("LATTICE_CLIENT_SECRET"),
    )
    parser.add_argument("--sandboxes-token", default=os.getenv("SANDBOXES_TOKEN", ""))
    args = parser.parse_args()

    from anduril import Lattice

    headers = {}
    if args.sandboxes_token:
        headers["anduril-sandbox-authorization"] = f"Bearer {args.sandboxes_token}"
    client = Lattice(
        base_url=f"https://{args.lattice_endpoint}",
        client_id=args.lattice_client_id,
        client_secret=args.lattice_client_secret,
        headers=headers,
    )

    run(client, args.asset_id, args.start_lat, args.start_lon)


if __name__ == "__main__":
    main()
