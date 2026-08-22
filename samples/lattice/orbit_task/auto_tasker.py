"""Watches Lattice's common operating picture and automatically tasks an
Orbit asset based on the entities in the environment -- the third of three
programs in this directory demonstrating automated tasking (see
README.md). Together with publish_poi.py (publishes a sensor
point-of-interest) and orbit_asset.py (a taskable asset that executes
Orbit tasks), these three demonstrate an automated tasking scenario:
a POI appears -> this watches for it -> it creates an Orbit task assigned
to the asset -> the asset flies to and orbits it.

Watches for TEMPLATE_SENSOR_POINT_OF_INTEREST entities (see
publish_poi.py) via stream_entities, and creates an Orbit task (see
spec.py/orbit.proto) centered on each new one, assigned to the asset named
by --asset-id. Tracks which POI entity_ids it's already tasked (in
memory, this-process-lifetime only) so the same POI doesn't get re-tasked
on every stream reconnect/heartbeat.

Usage:
    pip install -r requirements-lattice.txt
    export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
    export LATTICE_CLIENT_ID=...
    export LATTICE_CLIENT_SECRET=...
    export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes

    python samples/lattice/orbit_task/auto_tasker.py --asset-id orbit-asset-1
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid

from spec import build_orbit_specification

logger = logging.getLogger(__name__)

_POI_TEMPLATE = "TEMPLATE_SENSOR_POINT_OF_INTEREST"


def _task_poi(client, asset_id: str, poi_entity, radius_meters: float, altitude_meters: float, duration_seconds: float):
    from anduril import GoogleProtobufAny, Principal, Relations, System, TaskEntity

    position = poi_entity.location.position if poi_entity.location else None
    if position is None or position.latitude_degrees is None or position.longitude_degrees is None:
        logger.info("Skipping POI %s: no position yet", poi_entity.entity_id)
        return None

    spec = build_orbit_specification(
        latitude_degrees=position.latitude_degrees,
        longitude_degrees=position.longitude_degrees,
        radius_meters=radius_meters,
        altitude_meters=altitude_meters,
        duration_seconds=duration_seconds,
    )
    task_id = f"orbit-task-{uuid.uuid4()}"
    task = client.tasks.create_task(
        task_id=task_id,
        display_name=f"Orbit POI {poi_entity.entity_id}",
        specification=GoogleProtobufAny(**spec),
        relations=Relations(assignee=Principal(system=System(entity_id=asset_id, service_name="orbit-asset-sample"))),
        initial_entities=[TaskEntity(entity=poi_entity, snapshot=True)],
    )
    print(f"-> created task {task_id} assigning {asset_id} to orbit POI {poi_entity.entity_id}")
    return task


def watch(client, asset_id: str, radius_meters: float, altitude_meters: float, duration_seconds: float) -> None:
    already_tasked: set[str] = set()
    print(f"Watching for {_POI_TEMPLATE} entities to auto-task {asset_id}...")
    for event in client.entities.stream_entities(pre_existing_only=False):
        if event.event == "heartbeat":
            continue
        entity = event.entity
        if entity is None or entity.entity_id is None:
            continue
        if event.event_type == "EVENT_TYPE_DELETED":
            already_tasked.discard(entity.entity_id)
            continue
        template = entity.ontology.template if entity.ontology else None
        if template != _POI_TEMPLATE or entity.entity_id in already_tasked:
            continue
        already_tasked.add(entity.entity_id)
        try:
            _task_poi(client, asset_id, entity, radius_meters, altitude_meters, duration_seconds)
        except Exception:
            logger.exception("Failed to task POI %s", entity.entity_id)
            already_tasked.discard(entity.entity_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--asset-id", default="orbit-asset-1", help="Must match orbit_asset.py's --asset-id")
    parser.add_argument("--radius-meters", type=float, default=200.0)
    parser.add_argument("--altitude-meters", type=float, default=100.0)
    parser.add_argument("--duration-seconds", type=float, default=120.0)
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

    watch(client, args.asset_id, args.radius_meters, args.altitude_meters, args.duration_seconds)


if __name__ == "__main__":
    main()
