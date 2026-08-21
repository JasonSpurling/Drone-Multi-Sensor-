"""SQLAlchemy Core table definitions -- the single source of truth for the
database schema, portable across SQLite (the zero-config default) and
PostgreSQL (set DRONE_DATABASE_URL=postgresql+psycopg2://... for production
deployments that need concurrent-write throughput SQLite can't offer).

Timestamps are stored as ISO-8601 strings (not native TIMESTAMP columns) to
keep the exact naive-UTC datetime round-trip behavior the app already
relies on identical across both backends.
"""

from __future__ import annotations

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, MetaData, String, Table, Text

metadata = MetaData()

# A physical site/campus this deployment monitors. Every row-level table
# below carries a site_id, and every db.py query is scoped by it -- see
# app/sites.py for the "auto-create + migrate everything to a default
# site" startup behavior that keeps a pre-multi-site deployment's data
# and API keys working unchanged after upgrading.
site = Table(
    "site",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(200), nullable=False, unique=True),
)

zone = Table(
    "zone",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("site_id", Integer, ForeignKey("site.id")),
    Column("name", String(200), nullable=False),
    Column("zone_type", String(50), nullable=False),
    Column("polygon", Text, nullable=False),  # JSON list of [lat, lon] pairs
    Column("min_altitude_m", Float),
    Column("max_altitude_m", Float),
    Column("active", Integer, nullable=False, server_default="1"),  # 0/1
    Index("idx_zone_site_id", "site_id"),
)

track = Table(
    "track",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("site_id", Integer, ForeignKey("site.id")),
    Column("track_uid", String(64), nullable=False, unique=True),
    Column("first_seen", String(40), nullable=False),
    Column("last_seen", String(40), nullable=False),
    Column("status", String(20), nullable=False, server_default="active"),
    Column("classification", String(20), nullable=False, server_default="unknown"),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("altitude_m", Float),
    Column("heading_deg", Float),
    Column("speed_mps", Float),
    Column("position_uncertainty_m", Float),
    # list_tracks(status=...) -- called on essentially every detection
    # ingested (expire_stale_tracks scans ACTIVE/LOST tracks) and on every
    # dashboard poll (GET /api/tracks, unfiltered, every few seconds) --
    # filters on status and always orders by last_seen DESC; without this,
    # both become a full table scan + sort once a deployment accumulates
    # more than a handful of closed tracks.
    Index("idx_track_status_last_seen", "status", "last_seen"),
    # Every query above is also scoped by site_id now (see app/db.py) --
    # this composite index leads with it so a site-scoped query doesn't
    # regress to the unscoped index's scan-then-filter.
    Index("idx_track_site_status_last_seen", "site_id", "status", "last_seen"),
)

# IMM (Interacting Multiple Model) filter state for a track's motion
# estimate, kept separate from the public `track` row: ref_lat/ref_lon
# anchor the local tangent-plane frame the filter runs in (see
# app/geo.py), models is a JSON list of each mode's {x,y,vx,vy,covariance}
# (see app/imm.py -- CRUISE and MANEUVER, in that order), and
# mode_probabilities is a JSON [cruise_probability, maneuver_probability]
# pair -- all internal to the tracker, not exposed via the API.
track_kalman_state = Table(
    "track_kalman_state",
    metadata,
    Column("track_id", Integer, ForeignKey("track.id"), primary_key=True),
    Column("ref_lat", Float, nullable=False),
    Column("ref_lon", Float, nullable=False),
    Column("models", Text, nullable=False),
    Column("mode_probabilities", Text, nullable=False),
    Column("updated_at", String(40), nullable=False),
)

detection = Table(
    "detection",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("site_id", Integer, ForeignKey("site.id")),
    Column("sensor_id", String(100), nullable=False),
    Column("sensor_type", String(20), nullable=False),
    Column("timestamp", String(40), nullable=False),
    Column("track_id", Integer, ForeignKey("track.id")),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("altitude_m", Float),
    Column("azimuth_deg", Float),
    Column("range_m", Float),
    Column("confidence", Float, nullable=False, server_default="1.0"),
    Column("raw_data", Text),  # JSON blob
    # True if latitude/longitude were computed by app/georeference.py from
    # this sensor's azimuth/range report, rather than reported directly by
    # the sensor. Needed so a signed detection's signature (app/remote_id.py)
    # verifies against the position the sensor actually signed (None/None
    # for an azimuth/range-only sensor) rather than the position
    # georeferencing later filled in -- see canonical_message().
    Column("georeferenced", Integer, nullable=False, server_default="0"),
    Index("idx_detection_track_id", "track_id"),
    Index("idx_detection_timestamp", "timestamp"),
    Index("idx_detection_site_id", "site_id"),
)

incident = Table(
    "incident",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("site_id", Integer, ForeignKey("site.id")),
    Column("incident_uid", String(64), nullable=False, unique=True),
    Column("incident_type", String(50), nullable=False),
    Column("severity", String(20), nullable=False, server_default="low"),
    Column("status", String(20), nullable=False, server_default="open"),
    Column("track_id", Integer, ForeignKey("track.id")),
    Column("zone_id", Integer, ForeignKey("zone.id")),
    Column("opened_at", String(40), nullable=False),
    Column("closed_at", String(40)),
    Column("description", Text),
    Column("acknowledged_by", String(100)),
    Index("idx_incident_track_id", "track_id"),
    Index("idx_incident_zone_id", "zone_id"),
    Index("idx_incident_site_id", "site_id"),
)

# A registered sensor's fixed mounting position/orientation, used to
# georeference detections that report azimuth/range instead of lat/lon
# (see app/georeference.py).
#
# KNOWN LIMITATION: sensor_id stays the sole primary key (not (site_id,
# sensor_id)) -- making it composite would need rebuilding this table on
# upgrade (SQLite/PostgreSQL can't portably ALTER a table's primary key in
# place), which is a bigger migration than this pass takes on. Until that
# lands, sensor_id (and authorized_operator's operator_id, same reason)
# must stay globally unique across every site in one deployment, not just
# within a site -- namespace them (e.g. "site-a-radar-1") if you're
# running more than one site with independently-chosen sensor names.
sensor_registry = Table(
    "sensor_registry",
    metadata,
    Column("sensor_id", String(100), primary_key=True),
    Column("site_id", Integer, ForeignKey("site.id")),
    Column("sensor_type", String(20), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("altitude_m", Float),
    Column("azimuth_reference_deg", Float, nullable=False, server_default="0.0"),
    Column("active", Integer, nullable=False, server_default="1"),
    Index("idx_sensor_registry_site_id", "site_id"),
)

# Known/authorized drone operators (e.g. FAA Remote ID operator IDs) whose
# aircraft should be classified FRIENDLY rather than flagged as a threat.
# public_key is a base64-encoded Ed25519 public key: a detection claiming
# this operator_id must carry a signature verifiable against it (see
# app/remote_id.py, app/allowlist.py) -- an operator_id string alone is
# not enough to be trusted.
authorized_operator = Table(
    "authorized_operator",
    metadata,
    Column("operator_id", String(100), primary_key=True),
    Column("site_id", Integer, ForeignKey("site.id")),
    Column("name", String(200), nullable=False),
    Column("public_key", String(64)),
    Column("active", Integer, nullable=False, server_default="1"),
    Index("idx_authorized_operator_site_id", "site_id"),
)
