"""Maps one Anduril Lattice Entity (as the camelCase dict produced by
`entity.model_dump(by_alias=True, exclude_none=True)`, matching the SDK's
REST/JSON wire format) to a GeoJSON Feature for the map in
static/index.html -- matches Anduril's own "Entity visualizer" sample app.

Kept dependency-free (no `anduril` import), same split as
app/adapters/lattice.py vs lattice_bridge.py in the main app: unit-testable
without installing the SDK, server.py converts real SDK Entity objects to
this dict shape before calling this function.
"""

from __future__ import annotations


def entity_to_geojson_feature(entity: dict) -> dict | None:
    """Returns a GeoJSON Feature, or None for an entity with no position
    yet -- Lattice's Location/Position model has no useful representation
    of "exists but not localized" (e.g. a freshly-created taskable Asset
    entity that hasn't reported a position), and a Feature with no
    coordinates isn't valid GeoJSON.
    """
    position = (entity.get("location") or {}).get("position")
    if not position or position.get("latitudeDegrees") is None or position.get("longitudeDegrees") is None:
        return None

    aliases = entity.get("aliases") or {}
    mil_view = entity.get("milView") or {}
    ontology = entity.get("ontology") or {}

    # GeoJSON coordinate order is [longitude, latitude] -- the opposite of
    # how this app's own dashboard.html and every Lattice Position field
    # name it, a classic source of a silently-swapped-axes bug if not
    # called out explicitly here.
    coordinates = [position["longitudeDegrees"], position["latitudeDegrees"]]
    if position.get("altitudeHaeMeters") is not None:
        coordinates.append(position["altitudeHaeMeters"])

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": {
            "entity_id": entity.get("entityId"),
            "name": aliases.get("name") or entity.get("entityId"),
            "is_live": entity.get("isLive", False),
            "disposition": mil_view.get("disposition", "DISPOSITION_UNKNOWN"),
            "environment": mil_view.get("environment", "ENVIRONMENT_UNKNOWN"),
            "template": ontology.get("template", "TEMPLATE_INVALID"),
        },
    }


def entities_to_feature_collection(entities: dict) -> dict:
    """`entities` maps entity_id -> entity dict (the visualizer's own
    in-memory cache, see server.py). Skips entities with no position
    rather than raising, since a stale/incomplete entity shouldn't take
    down the whole map.
    """
    features = []
    for entity in entities.values():
        feature = entity_to_geojson_feature(entity)
        if feature is not None:
            features.append(feature)
    return {"type": "FeatureCollection", "features": features}
