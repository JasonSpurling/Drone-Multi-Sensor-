"""Domain data models for the drone multi-sensor system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SensorType(str, Enum):
    RADAR = "radar"
    RF = "rf"
    ACOUSTIC = "acoustic"
    CAMERA = "camera"
    ADSB = "adsb"
    OTHER = "other"


class SensorStatus(str, Enum):
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"


class TrackStatus(str, Enum):
    ACTIVE = "active"
    LOST = "lost"
    CLOSED = "closed"


class Classification(str, Enum):
    UNKNOWN = "unknown"
    DRONE = "drone"
    BIRD = "bird"
    AIRCRAFT = "aircraft"
    FRIENDLY = "friendly"


class IncidentType(str, Enum):
    ZONE_INCURSION = "zone_incursion"
    UNAUTHORIZED_FLIGHT = "unauthorized_flight"
    LOSS_OF_TRACK = "loss_of_track"
    SENSOR_FAULT = "sensor_fault"
    OTHER = "other"


class IncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ZoneType(str, Enum):
    RESTRICTED = "restricted"
    NO_FLY = "no_fly"
    MONITORING = "monitoring"
    SAFE = "safe"


class Detection(BaseModel):
    """A single raw detection reported by one sensor."""

    id: int | None = None
    sensor_id: str = Field(min_length=1, max_length=100)
    sensor_type: SensorType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    track_id: int | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    altitude_m: float | None = None
    azimuth_deg: float | None = None
    range_m: float | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    raw_data: dict | None = None


class Track(BaseModel):
    """A fused sequence of detections believed to be the same object."""

    id: int | None = None
    track_uid: str
    first_seen: datetime
    last_seen: datetime
    status: TrackStatus = TrackStatus.ACTIVE
    classification: Classification = Classification.UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None


class Incident(BaseModel):
    """An actionable event raised from track/zone analysis."""

    id: int | None = None
    incident_uid: str
    incident_type: IncidentType
    severity: IncidentSeverity = IncidentSeverity.LOW
    status: IncidentStatus = IncidentStatus.OPEN
    track_id: int | None = None
    zone_id: int | None = None
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
    description: str | None = None


class Zone(BaseModel):
    """A geofenced area of interest, e.g. a no-fly zone."""

    id: int | None = None
    name: str
    zone_type: ZoneType
    polygon: list[tuple[float, float]] = Field(
        description="List of (latitude, longitude) vertices"
    )
    min_altitude_m: float | None = None
    max_altitude_m: float | None = None
    active: bool = True


class SensorHealth(BaseModel):
    """Derived liveness status for a sensor, based on its most recent detection."""

    sensor_id: str
    sensor_type: SensorType
    last_seen: datetime
    status: SensorStatus
