"""Maps one AIS (Automatic Identification System) vessel position report
to the kwargs for Anduril Lattice's Entities API `publish_entity` call --
matching Anduril's own "Integrate maritime AIS position data" sample app,
which models a surface vessel as a periodically-updated Entity.

Kept dependency-free (no `anduril` import), same split as
app/adapters/lattice.py vs lattice_bridge.py in the main app: this module
is unit-testable without installing the SDK, ais_publisher.py imports
`anduril` lazily and turns these plain dicts into the SDK's typed objects.

Record format: AIS is normally decoded from raw NMEA 0183 VDM/VDO
sentences off a receiver -- decoding that wire format from scratch is a
large, separate undertaking (AIVDM 6-bit ASCII armoring, dozens of message
types) and out of scope for a sample app. This works from the *decoded*
shape any AIS receiver/decoder or historical extract already produces --
one JSON object per position report:

    {
        "mmsi": "366123456",
        "vessel_name": "MV EXAMPLE",
        "ship_type": "cargo",
        "latitude": 37.8199,
        "longitude": -122.4783,
        "sog_knots": 12.4,
        "cog_degrees": 271.0,
        "timestamp": "2026-08-22T06:00:00"
    }

See sample_vessels.jsonl for a small bundled fixture (fabricated
coordinates/MMSIs, not real vessel data) to try this against without a
real AIS feed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Same reasoning as app/adapters/lattice.py's ENTITY_EXPIRY_SECONDS --
# Lattice drops an entity once its expiry passes, so a publisher must
# republish faster than this. AIS position reports for a moving vessel
# typically arrive every few seconds to a couple of minutes depending on
# vessel class/speed, so this is generous relative to that.
ENTITY_EXPIRY_SECONDS = 180

_REQUIRED_FIELDS = ("mmsi", "latitude", "longitude")


def parse_ais_record(raw: dict) -> dict:
    """Validates and normalizes one decoded AIS position report. Raises
    ValueError with a specific reason for a record missing what this
    sample actually needs (mmsi + position) -- vessel_name/ship_type/
    sog_knots/cog_degrees/timestamp are all optional, since not every AIS
    message type carries all of them (a Class B position report, for
    instance, has no navigational status).
    """
    missing = [f for f in _REQUIRED_FIELDS if raw.get(f) in (None, "")]
    if missing:
        raise ValueError(f"AIS record missing required field(s): {', '.join(missing)}")
    return {
        "mmsi": str(raw["mmsi"]),
        "vessel_name": raw.get("vessel_name"),
        "ship_type": raw.get("ship_type"),
        "latitude": float(raw["latitude"]),
        "longitude": float(raw["longitude"]),
        "sog_knots": float(raw["sog_knots"]) if raw.get("sog_knots") is not None else None,
        "cog_degrees": float(raw["cog_degrees"]) if raw.get("cog_degrees") is not None else None,
        "timestamp": raw.get("timestamp"),
    }


def ais_record_to_publish_entity_kwargs(
    record: dict, integration_name: str = "ais-sample", now: datetime | None = None
) -> dict:
    """Returns the kwargs for `client.entities.publish_entity(**kwargs)`,
    as plain nested dicts (see module docstring). `record` must already be
    normalized via parse_ais_record(). `now` is injectable for tests;
    defaults to the real current UTC time.
    """
    if now is None:
        now = datetime.utcnow()

    position = {"latitude_degrees": record["latitude"], "longitude_degrees": record["longitude"]}
    location: dict = {"position": position}
    if record["sog_knots"] is not None:
        # 1 knot = 0.514444 m/s -- Lattice's Location.speed_mps is metric.
        location["speed_mps"] = record["sog_knots"] * 0.514444

    display_name = record["vessel_name"] or f"Vessel {record['mmsi']}"

    return {
        "entity_id": f"ais-vessel-{record['mmsi']}",
        "is_live": True,
        "expiry_time": now + timedelta(seconds=ENTITY_EXPIRY_SECONDS),
        "aliases": {"name": display_name},
        "location": location,
        "mil_view": {
            # AIS is a cooperative/self-reported system carried by
            # commercial and other legitimate traffic -- a vessel actively
            # broadcasting its own identity isn't inherently suspicious
            # the way an unidentified radar contact would be, so this
            # defaults to NEUTRAL rather than SUSPICIOUS/UNKNOWN.
            "disposition": "DISPOSITION_NEUTRAL",
            "environment": "ENVIRONMENT_SURFACE",
        },
        "provenance": {
            "integration_name": integration_name,
            "data_type": record.get("ship_type") or "vessel",
            "source_id": record["mmsi"],
            "source_update_time": record.get("timestamp") or now.isoformat(),
        },
        "ontology": {"template": "TEMPLATE_TRACK"},
    }
