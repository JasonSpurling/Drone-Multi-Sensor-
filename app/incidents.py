"""Incident creation from zone-membership rules, both for a track's actual
current position and its Kalman-projected future position.
"""

from __future__ import annotations

import logging
import math
import uuid

from app.config import PREDICTIVE_HORIZON_SECONDS
from app.db import create_incident, get_open_incident
from app.geo import local_m_to_latlon
from app.metrics import incidents_opened_total
from app.notifications import notify_incident
from app.models import (
    Classification,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    Track,
    Zone,
    ZoneType,
)
from app.util import utcnow
from app.zones import zones_containing_point

logger = logging.getLogger(__name__)

_SEVERITY_BY_CLASSIFICATION = {
    Classification.DRONE: IncidentSeverity.HIGH,
    Classification.UNKNOWN: IncidentSeverity.MEDIUM,
    Classification.AIRCRAFT: IncidentSeverity.LOW,
    Classification.BIRD: IncidentSeverity.LOW,
    Classification.FRIENDLY: IncidentSeverity.LOW,
}

# A track's estimated speed below this is treated as noise around zero
# rather than genuine motion, so it doesn't drive a spurious predicted
# projection off in a random direction.
_MIN_SPEED_FOR_PROJECTION_MPS = 1.0


def _open_incident(
    track: Track, zone: Zone, incident_type: IncidentType, description: str
) -> Incident | None:
    if get_open_incident(track.id, zone.id, incident_type.value) is not None:
        return None
    severity = _SEVERITY_BY_CLASSIFICATION.get(track.classification, IncidentSeverity.MEDIUM)
    incident = create_incident(
        Incident(
            incident_uid=str(uuid.uuid4()),
            incident_type=incident_type,
            severity=severity,
            status=IncidentStatus.OPEN,
            track_id=track.id,
            zone_id=zone.id,
            opened_at=utcnow(),
            description=description,
        )
    )
    logger.warning(
        "Incident opened (%s): track %s, zone '%s' (severity=%s)",
        incident_type.value, track.track_uid, zone.name, severity.value,
    )
    incidents_opened_total.labels(incident_type=incident_type.value, severity=severity.value).inc()
    notify_incident(incident)
    return incident


def check_zone_incidents(track: Track) -> list[Incident]:
    """Open a zone-incursion incident for each restricted zone a track's
    current position falls inside, unless one is already open for that
    track/zone pair.
    """
    if track.id is None or track.latitude is None or track.longitude is None:
        return []

    opened: list[Incident] = []
    for zone in zones_containing_point(track.latitude, track.longitude, track.altitude_m):
        if zone.zone_type != ZoneType.RESTRICTED or zone.id is None:
            continue
        incident = _open_incident(
            track, zone, IncidentType.ZONE_INCURSION,
            f"Track {track.track_uid} entered restricted zone '{zone.name}'",
        )
        if incident is not None:
            opened.append(incident)
    return opened


def check_predicted_incursions(track: Track) -> list[Incident]:
    """Project a track's position forward by PREDICTIVE_HORIZON_SECONDS
    using its Kalman-filtered velocity, and open an early-warning incident
    for any restricted zone the projection enters that the track isn't
    already inside (that's check_zone_incidents' job).
    """
    if (
        track.id is None
        or track.latitude is None
        or track.longitude is None
        or track.speed_mps is None
        or track.heading_deg is None
        or track.speed_mps < _MIN_SPEED_FOR_PROJECTION_MPS
    ):
        return []

    heading_rad = math.radians(track.heading_deg)
    east_m = track.speed_mps * math.sin(heading_rad) * PREDICTIVE_HORIZON_SECONDS
    north_m = track.speed_mps * math.cos(heading_rad) * PREDICTIVE_HORIZON_SECONDS
    projected_lat, projected_lon = local_m_to_latlon(east_m, north_m, track.latitude, track.longitude)

    current_zone_ids = {
        zone.id for zone in zones_containing_point(track.latitude, track.longitude, track.altitude_m)
    }

    opened: list[Incident] = []
    for zone in zones_containing_point(projected_lat, projected_lon, track.altitude_m):
        if zone.zone_type != ZoneType.RESTRICTED or zone.id is None or zone.id in current_zone_ids:
            continue
        incident = _open_incident(
            track, zone, IncidentType.PREDICTED_INCURSION,
            f"Track {track.track_uid} projected to enter restricted zone '{zone.name}' "
            f"within {PREDICTIVE_HORIZON_SECONDS:.0f}s",
        )
        if incident is not None:
            opened.append(incident)
    return opened
