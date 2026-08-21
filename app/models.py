"""Domain data models for the drone multi-sensor system."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.util import utcnow


class SensorType(StrEnum):
    RADAR = "radar"
    RF = "rf"
    ACOUSTIC = "acoustic"
    CAMERA = "camera"
    ADSB = "adsb"
    OTHER = "other"


class SensorStatus(StrEnum):
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"


class TrackStatus(StrEnum):
    ACTIVE = "active"
    LOST = "lost"
    CLOSED = "closed"


class Classification(StrEnum):
    UNKNOWN = "unknown"
    DRONE = "drone"
    BIRD = "bird"
    AIRCRAFT = "aircraft"
    FRIENDLY = "friendly"


class IncidentType(StrEnum):
    ZONE_INCURSION = "zone_incursion"
    PREDICTED_INCURSION = "predicted_incursion"
    UNAUTHORIZED_FLIGHT = "unauthorized_flight"
    LOSS_OF_TRACK = "loss_of_track"
    SENSOR_FAULT = "sensor_fault"
    LOITERING = "loitering"
    FORMATION = "formation"
    SHADOWING = "shadowing"
    OTHER = "other"


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ZoneType(StrEnum):
    RESTRICTED = "restricted"
    NO_FLY = "no_fly"
    MONITORING = "monitoring"
    SAFE = "safe"


class Site(BaseModel):
    """A physical site/campus this deployment monitors. Every row-level
    record (Track, Detection, Incident, Zone, SensorRegistration,
    AuthorizedOperator) belongs to exactly one Site -- see app/sites.py
    and app/auth.py's Principal.site_id for how a request's site is
    determined (from its API key, not client-supplied).
    """

    id: int | None = None
    name: str = Field(min_length=1, max_length=200)


class Detection(BaseModel):
    """A single raw detection reported by one sensor."""

    id: int | None = None
    # Set server-side from the authenticated request's Principal.site_id
    # (see app/auth.py) -- never client-supplied, the same reasoning as
    # georeferenced below: a client claiming a site it doesn't hold a key
    # for would let it write into another site's data.
    site_id: int | None = None
    sensor_id: str = Field(min_length=1, max_length=100)
    sensor_type: SensorType
    timestamp: datetime = Field(default_factory=utcnow)
    track_id: int | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    altitude_m: float | None = None
    azimuth_deg: float | None = None
    range_m: float | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    raw_data: dict | None = None
    georeferenced: bool = Field(
        default=False,
        description="Set server-side by app/georeference.py when latitude/longitude were computed "
        "from this sensor's azimuth/range report rather than reported directly. Any client-supplied "
        "value is discarded on ingest (see app/api/detections.py) -- a signed detection's signature "
        "must verify against what the sensor actually signed, not a claim the client controls.",
    )


class Track(BaseModel):
    """A fused sequence of detections believed to be the same object."""

    id: int | None = None
    site_id: int | None = None
    track_uid: str
    first_seen: datetime
    last_seen: datetime
    status: TrackStatus = TrackStatus.ACTIVE
    classification: Classification = Classification.UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    heading_deg: float | None = Field(
        default=None, description="Kalman-filtered heading, degrees clockwise from true north"
    )
    speed_mps: float | None = Field(default=None, description="Kalman-filtered ground speed")
    position_uncertainty_m: float | None = Field(
        default=None, description="1-sigma radial position uncertainty from the Kalman filter"
    )
    maneuver_probability: float | None = Field(
        default=None,
        description="IMM MANEUVER-mode probability (0-1): how confident the tracker is that "
        "this object is currently maneuvering (turning/accelerating) rather than flying straight",
    )


class Incident(BaseModel):
    """An actionable event raised from track/zone analysis."""

    id: int | None = None
    site_id: int | None = None
    incident_uid: str
    incident_type: IncidentType
    severity: IncidentSeverity = IncidentSeverity.LOW
    status: IncidentStatus = IncidentStatus.OPEN
    track_id: int | None = None
    zone_id: int | None = None
    opened_at: datetime = Field(default_factory=utcnow)
    closed_at: datetime | None = None
    description: str | None = None
    acknowledged_by: str | None = Field(
        default=None, description="Identity (API key name/role) that acknowledged this incident"
    )


class Zone(BaseModel):
    """A geofenced area of interest, e.g. a no-fly zone."""

    id: int | None = None
    site_id: int | None = None
    name: str
    zone_type: ZoneType
    polygon: list[tuple[float, float]] = Field(
        description="List of (latitude, longitude) vertices"
    )
    min_altitude_m: float | None = None
    max_altitude_m: float | None = None
    active: bool = True


class ZoneInput(BaseModel):
    """A zone as an admin creates/edits it via POST/PUT /api/zones -- same
    shape as Zone minus id/site_id, which are always server-assigned (see
    app/api/zones.py), the same reasoning as SensorRegistrationInput and
    AuthorizedOperatorInput above.
    """

    name: str = Field(min_length=1, max_length=200)
    zone_type: ZoneType
    polygon: list[tuple[float, float]] = Field(
        min_length=3, description="List of (latitude, longitude) vertices -- at least 3 to form a real polygon"
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


class SensorRegistrationInput(BaseModel):
    """A sensor's fixed mounting position/orientation, used to georeference
    detections that report azimuth/range instead of lat/lon directly (see
    app/georeference.py).
    """

    sensor_type: SensorType
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float | None = None
    azimuth_reference_deg: float = Field(
        default=0.0,
        description="Compass bearing (degrees) the sensor's azimuth_deg=0 points to",
    )
    active: bool = True


class SensorRegistration(SensorRegistrationInput):
    sensor_id: str
    site_id: int | None = None


class AuthorizedOperatorInput(BaseModel):
    """A known/authorized drone operator (e.g. an FAA Remote ID operator
    ID) whose aircraft should be classified FRIENDLY. See app/allowlist.py.
    public_key is a base64-encoded Ed25519 public key (see
    app/remote_id.py) -- a detection claiming this operator_id must carry
    a signature verifiable against it to be trusted; the ID string alone
    proves nothing.
    """

    name: str = Field(min_length=1, max_length=200)
    public_key: str | None = Field(
        default=None, description="Base64-encoded Ed25519 public key (see app/remote_id.py)"
    )
    active: bool = True


class AuthorizedOperator(AuthorizedOperatorInput):
    operator_id: str
    site_id: int | None = None


class AuditLogEntry(BaseModel):
    """One recorded admin action -- see app/schema.py's audit_log table
    for which actions get recorded and why.
    """

    id: int | None = None
    site_id: int | None = None
    occurred_at: datetime
    actor: str
    action: str
    target: str | None = None
    detail: str | None = None


class ApiKeyStatus(BaseModel):
    """One configured key's non-secret metadata plus its recorded usage,
    for GET /api/admin/keys -- never the raw key itself (see
    app/auth.py's _configured_keys(); the raw key only ever exists
    in-process, matched against the caller's X-API-Key header).
    """

    label: str
    role: str
    site: str | None = None
    revoked: bool = False
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    use_count: int = 0
