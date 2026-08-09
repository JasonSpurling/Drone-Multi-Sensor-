"""Incident creation from zone-membership rules."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from app.db import create_incident, get_open_incident
from app.models import (
    Classification,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    Track,
    ZoneType,
)
from app.zones import zones_containing_point

logger = logging.getLogger(__name__)

_SEVERITY_BY_CLASSIFICATION = {
    Classification.DRONE: IncidentSeverity.HIGH,
    Classification.UNKNOWN: IncidentSeverity.MEDIUM,
    Classification.AIRCRAFT: IncidentSeverity.LOW,
    Classification.BIRD: IncidentSeverity.LOW,
    Classification.FRIENDLY: IncidentSeverity.LOW,
}


def check_zone_incidents(track: Track) -> list[Incident]:
    """Open a zone-incursion incident for each restricted zone a track's
    current position falls inside, unless one is already open for that
    track/zone pair.
    """
    if track.id is None or track.latitude is None or track.longitude is None:
        return []

    opened: list[Incident] = []
    for zone in zones_containing_point(track.latitude, track.longitude):
        if zone.zone_type != ZoneType.RESTRICTED or zone.id is None:
            continue
        if get_open_incident(track.id, zone.id, IncidentType.ZONE_INCURSION.value) is not None:
            continue
        severity = _SEVERITY_BY_CLASSIFICATION.get(track.classification, IncidentSeverity.MEDIUM)
        incident = create_incident(
            Incident(
                incident_uid=str(uuid.uuid4()),
                incident_type=IncidentType.ZONE_INCURSION,
                severity=severity,
                status=IncidentStatus.OPEN,
                track_id=track.id,
                zone_id=zone.id,
                opened_at=datetime.utcnow(),
                description=f"Track {track.track_uid} entered restricted zone '{zone.name}'",
            )
        )
        logger.warning(
            "Incident opened: track %s entered restricted zone '%s' (severity=%s)",
            track.track_uid, zone.name, severity.value,
        )
        opened.append(incident)
    return opened
