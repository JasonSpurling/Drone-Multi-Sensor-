"""Maps this tracker's Track model to the kwargs for Anduril Lattice's
Entities API `publish_entity` call -- the reverse direction of every other
module in app/adapters/: those bring third-party sensor data IN as
Detections, this pushes OUR fused tracks OUT to a downstream Lattice
deployment as Entities, so this app's tracker can appear as one more
contributing source on Lattice's common operating picture.

Kept dependency-free (no `anduril` import) so the mapping logic is
unit-testable without installing the SDK -- app/adapters/lattice_bridge.py
imports `anduril` lazily and turns these plain dicts into the SDK's actual
typed objects, the same split as app/adapters/mavlink.py (pure payload
builder) vs mavlink_bridge.py (the pymavlink-dependent script).
"""

from __future__ import annotations

from datetime import timedelta

from app.models import Classification, Track

# How far past "now" each published entity's expiry_time is set -- Lattice
# drops an entity from the COP once its expiry passes, so the bridge must
# republish faster than this to keep a live track visible. Generous
# relative to the bridge's default poll interval so a single missed/slow
# poll cycle doesn't make the track flicker out of Lattice's picture.
ENTITY_EXPIRY_SECONDS = 30

# This app's Classification has no HOSTILE concept (it's a sensor fusion
# tracker, not a threat-assessment system) -- DRONE/unknown aerial contacts
# map to SUSPICIOUS (Lattice's "warrants attention" bucket) rather than
# HOSTILE, which would overclaim an intent judgment this app never makes.
_DISPOSITION_BY_CLASSIFICATION = {
    Classification.FRIENDLY: "DISPOSITION_FRIENDLY",
    Classification.DRONE: "DISPOSITION_SUSPICIOUS",
    Classification.UNKNOWN: "DISPOSITION_UNKNOWN",
    Classification.BIRD: "DISPOSITION_NEUTRAL",
    Classification.AIRCRAFT: "DISPOSITION_NEUTRAL",
}


def track_to_publish_entity_kwargs(
    track: Track, integration_name: str = "drone-multi-sensor", now=None
) -> dict | None:
    """Returns the kwargs for `client.entities.publish_entity(**kwargs)`,
    as plain nested dicts (see module docstring for why). Returns None for
    a track with no position yet -- Lattice's Position/Location model has
    no useful representation of "detected but not yet localized", and a
    freshly-created track can briefly have first_seen/last_seen set but
    latitude/longitude still None before its first Kalman update.

    `now` is injectable for tests; defaults to the real current UTC time.
    """
    if track.latitude is None or track.longitude is None:
        return None

    from app.util import utcnow

    now = utcnow() if now is None else now

    position: dict = {
        "latitude_degrees": track.latitude,
        "longitude_degrees": track.longitude,
    }
    if track.altitude_m is not None:
        position["altitude_hae_meters"] = track.altitude_m

    location: dict = {"position": position}
    if track.speed_mps is not None:
        location["speed_mps"] = track.speed_mps

    return {
        "entity_id": f"drone-multi-sensor-{track.track_uid}",
        "is_live": track.status.value == "active",
        "expiry_time": now + timedelta(seconds=ENTITY_EXPIRY_SECONDS),
        "aliases": {"name": f"Track {track.track_uid}"},
        "location": location,
        "mil_view": {
            "disposition": _DISPOSITION_BY_CLASSIFICATION.get(track.classification, "DISPOSITION_UNKNOWN"),
            "environment": "ENVIRONMENT_AIR",
        },
        "provenance": {
            "integration_name": integration_name,
            "data_type": track.classification.value,
            "source_id": track.track_uid,
            "source_update_time": track.last_seen,
        },
        "ontology": {"template": "TEMPLATE_TRACK"},
    }
