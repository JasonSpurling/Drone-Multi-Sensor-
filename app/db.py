"""Database engine and storage helpers, portable across SQLite (default)
and PostgreSQL (DRONE_DATABASE_URL). Queries are plain SQL via SQLAlchemy
Core's text(), not the ORM -- the schema is small and the raw-SQL shape
made porting from the original sqlite3-only version mechanical and easy to
verify against both backends.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import bindparam, create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine

from app.config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE
from app.models import AuditLogEntry, Detection, Incident, Site, Track, Zone
from app.schema import metadata
from app.util import utcnow

# pool_size/max_overflow are QueuePool-specific -- SQLite doesn't use
# QueuePool (SQLAlchemy defaults it to NullPool/SingletonThreadPool
# depending on the URL), and passing those kwargs to an engine using a pool
# class that doesn't accept them raises a TypeError. Only apply them for a
# real (PostgreSQL) connection pool.
_engine_kwargs: dict = {"future": True}
if not DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["pool_size"] = DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = DB_MAX_OVERFLOW

def ensure_sqlite_directory_exists(database_url: str) -> None:
    """SQLite opens the database file itself but never creates a missing
    *parent* directory (unlike most "just works" expectations) -- on a
    completely fresh checkout, the default data/ directory doesn't exist
    yet (it's gitignored, and git doesn't track empty directories even if
    it weren't), so the very first connection attempt fails with "unable
    to open database file" before init_db() ever gets a chance to run.
    A no-op for a non-sqlite URL (nothing to create) or an in-memory
    database (":memory:", used by the test suite -- no file/directory at
    all).
    """
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


ensure_sqlite_directory_exists(DATABASE_URL)
engine: Engine = create_engine(DATABASE_URL, **_engine_kwargs)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    if engine.dialect.name == "sqlite":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


@contextmanager
def db_session() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn


# Columns added to existing tables after their initial release.
# metadata.create_all only creates a table on a fresh database, so an
# existing database needs these added explicitly to pick them up.
_TABLE_MIGRATION_COLUMNS = {
    "track": {
        "heading_deg": "REAL",
        "speed_mps": "REAL",
        "position_uncertainty_m": "REAL",
        "maneuver_probability": "REAL",
        "site_id": "INTEGER",
        "aircraft_category": "VARCHAR(2)",
    },
    "authorized_operator": {
        "public_key": "VARCHAR(64)",
        "site_id": "INTEGER",
    },
    "detection": {
        "georeferenced": "INTEGER DEFAULT 0",
        "site_id": "INTEGER",
        "human_label": "VARCHAR(20)",
    },
    "zone": {"site_id": "INTEGER"},
    "incident": {"site_id": "INTEGER", "related_track_id": "INTEGER"},
    "sensor_registry": {"site_id": "INTEGER"},
}


def _migrate_table_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _TABLE_MIGRATION_COLUMNS.items():
            if table not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _migrate_kalman_state_table() -> None:
    """track_kalman_state's shape changed when tracking.py moved from a
    single constant-velocity filter to an IMM (app/imm.py): the old
    x_m/y_m/vx_mps/vy_mps/covariance columns became a `models` JSON list
    plus `mode_probabilities`. This table holds only ephemeral, derivable
    filter state (not audit data), so on detecting the old shape we just
    drop and let create_all recreate it -- any in-flight tracks simply
    reinitialize their filter on their next detection.
    """
    inspector = inspect(engine)
    if "track_kalman_state" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("track_kalman_state")}
    if "x_m" in existing and "models" not in existing:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE track_kalman_state"))


def _migrate_indexes() -> None:
    """metadata.create_all only creates indexes as part of creating a new
    table -- it doesn't add an index to a table that already exists (unlike
    a fresh database, which gets it for free from the track table's
    definition in schema.py). CREATE INDEX IF NOT EXISTS is supported by
    both SQLite and PostgreSQL, so this is safe to run on every startup.
    """
    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_track_status_last_seen ON track (status, last_seen)")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_incident_related_track_id "
                "ON incident (related_track_id)"
            )
        )


def _backfill_site_id_columns() -> None:
    """Every row that predates the site_id column (an upgraded deployment)
    gets assigned to the default site, so existing data and API keys keep
    working unchanged -- see app/sites.py. A no-op on a fresh database
    (nothing to backfill) or a re-run (WHERE site_id IS NULL matches
    nothing once already backfilled).
    """
    from app.sites import ensure_default_site

    default_site_id = ensure_default_site()
    # Every table that carries a site_id column, each statement spelled
    # out (rather than interpolating a table name into one f-string
    # query) to avoid the dynamic-SQL shape entirely. Every fresh row
    # from here on gets a real site_id from the request's Principal (see
    # app/auth.py), never a default-backfilled one -- this only matters
    # for rows that predate the site_id column (an upgraded deployment).
    statements = (
        "UPDATE zone SET site_id = :site_id WHERE site_id IS NULL",
        "UPDATE track SET site_id = :site_id WHERE site_id IS NULL",
        "UPDATE detection SET site_id = :site_id WHERE site_id IS NULL",
        "UPDATE incident SET site_id = :site_id WHERE site_id IS NULL",
        "UPDATE sensor_registry SET site_id = :site_id WHERE site_id IS NULL",
        "UPDATE authorized_operator SET site_id = :site_id WHERE site_id IS NULL",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement), {"site_id": default_site_id})


def init_db() -> None:
    _migrate_kalman_state_table()
    metadata.create_all(engine, checkfirst=True)
    _migrate_table_columns()
    _migrate_indexes()
    _backfill_site_id_columns()


# --- Site helpers -----------------------------------------------------

def create_site(name: str) -> Site:
    with db_session() as conn:
        row = conn.execute(
            text("INSERT INTO site (name) VALUES (:name) RETURNING id"), {"name": name}
        ).one()
    return Site(id=row.id, name=name)


def get_site(site_id: int) -> Site | None:
    with db_session() as conn:
        row = conn.execute(text("SELECT * FROM site WHERE id = :id"), {"id": site_id}).mappings().fetchone()
    return Site(id=row["id"], name=row["name"]) if row else None


def get_site_by_name(name: str) -> Site | None:
    with db_session() as conn:
        row = conn.execute(text("SELECT * FROM site WHERE name = :name"), {"name": name}).mappings().fetchone()
    return Site(id=row["id"], name=row["name"]) if row else None


def list_sites() -> list[Site]:
    with db_session() as conn:
        rows = conn.execute(text("SELECT * FROM site ORDER BY name")).mappings().all()
    return [Site(id=row["id"], name=row["name"]) for row in rows]


# --- Audit log ----------------------------------------------------------

def record_audit(
    *, site_id: int | None, actor: str, action: str, target: str | None = None, detail: str | None = None
) -> None:
    """Records one admin action. Called from the request handler that just
    performed it (see app/api/sensor_registry.py, authorized_operators.py,
    sites.py, incidents.py) -- not from db.py's own mutation helpers, so
    what gets logged stays an explicit decision at each call site rather
    than "every UPDATE anywhere," most of which (a track's Kalman state
    updating on each detection, for instance) isn't an admin action at all.
    """
    with db_session() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (site_id, occurred_at, actor, action, target, detail) "
                "VALUES (:site_id, :occurred_at, :actor, :action, :target, :detail)"
            ),
            {
                "site_id": site_id,
                "occurred_at": utcnow().isoformat(),
                "actor": actor,
                "action": action,
                "target": target,
                "detail": detail,
            },
        )


def list_audit_log(limit: int = 200, offset: int = 0) -> list[AuditLogEntry]:
    """Deployment-wide, like list_sites() -- not scoped to one caller's
    site, and for the same reason app/api/sites.py's endpoints aren't:
    only an admin key can reach this (see app/api/audit_log.py), and an
    admin who can create/list every site in the deployment already isn't
    confined to one site's view of anything else admin-shaped.
    """
    with db_session() as conn:
        rows = (
            conn.execute(
                text("SELECT * FROM audit_log ORDER BY occurred_at DESC LIMIT :limit OFFSET :offset"),
                {"limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )
    return [
        AuditLogEntry(
            id=row["id"],
            site_id=row["site_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            actor=row["actor"],
            action=row["action"],
            target=row["target"],
            detail=row["detail"],
        )
        for row in rows
    ]


# --- API key usage --------------------------------------------------------

def record_key_usage(key_hash: str, *, when: datetime) -> None:
    """Upsert-style: create the row on first use, otherwise bump
    use_count and last_used_at. Called from app/auth.py's
    record_key_usage() wrapper, which throttles how often this actually
    runs per key -- see that function's docstring for why (this is called
    from the request-auth hot path, including every detection POST).
    """
    # ON CONFLICT ... DO UPDATE is standard, identical syntax on both
    # SQLite (3.24+) and PostgreSQL -- this app already relies on RETURNING
    # (SQLite 3.35+) elsewhere, so no extra version floor is introduced here.
    with db_session() as conn:
        conn.execute(
            text(
                "INSERT INTO api_key_usage (key_hash, last_used_at, use_count) "
                "VALUES (:key_hash, :when, 1) "
                "ON CONFLICT (key_hash) DO UPDATE SET "
                "last_used_at = excluded.last_used_at, use_count = api_key_usage.use_count + 1"
            ),
            {"key_hash": key_hash, "when": when.isoformat()},
        )


def get_key_usage(key_hashes: list[str]) -> dict[str, tuple[datetime, int]]:
    """key_hash -> (last_used_at, use_count) for whichever of the given
    hashes have ever been recorded. Looking up by a caller-supplied list
    (rather than returning every row) keeps this from ever needing to
    reverse a hash back to a key -- the caller (GET /api/admin/keys)
    already knows every configured key and just wants each one's usage.
    """
    if not key_hashes:
        return {}
    with db_session() as conn:
        rows = (
            conn.execute(
                text("SELECT * FROM api_key_usage WHERE key_hash IN :hashes").bindparams(
                    bindparam("hashes", expanding=True)
                ),
                {"hashes": key_hashes},
            )
            .mappings()
            .all()
        )
    return {row["key_hash"]: (datetime.fromisoformat(row["last_used_at"]), row["use_count"]) for row in rows}


def count_active_tracks_all_sites() -> int:
    """Deployment-wide count, not scoped to one site -- for GET /metrics,
    a Prometheus scrape endpoint with no per-request Principal (it's
    conventionally left unauthenticated) reporting on the whole
    deployment's operational health, not any one site's.
    """
    with db_session() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM track WHERE status = :status"), {"status": "active"}
        ).scalar_one()


def count_open_incidents_all_sites() -> int:
    """See count_active_tracks_all_sites -- same reasoning, deployment-wide."""
    with db_session() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM incident WHERE status = :status"), {"status": "open"}
        ).scalar_one()


# --- Detection helpers -----------------------------------------------------

def create_detection(detection: Detection) -> Detection:
    if detection.site_id is None:
        raise ValueError("create_detection requires detection.site_id to be set")
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO detection
                    (site_id, sensor_id, sensor_type, timestamp, track_id, latitude, longitude,
                     altitude_m, azimuth_deg, range_m, confidence, raw_data, georeferenced)
                VALUES (:site_id, :sensor_id, :sensor_type, :timestamp, :track_id, :latitude, :longitude,
                        :altitude_m, :azimuth_deg, :range_m, :confidence, :raw_data, :georeferenced)
                RETURNING id
                """
            ),
            {
                "site_id": detection.site_id,
                "sensor_id": detection.sensor_id,
                "sensor_type": detection.sensor_type.value,
                "timestamp": detection.timestamp.isoformat(),
                "track_id": detection.track_id,
                "latitude": detection.latitude,
                "longitude": detection.longitude,
                "altitude_m": detection.altitude_m,
                "azimuth_deg": detection.azimuth_deg,
                "range_m": detection.range_m,
                "confidence": detection.confidence,
                "raw_data": json.dumps(detection.raw_data) if detection.raw_data is not None else None,
                "georeferenced": int(detection.georeferenced),
            },
        ).one()
        detection.id = row.id
    return detection


def get_detection(detection_id: int, site_id: int) -> Detection | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM detection WHERE id = :id AND site_id = :site_id"),
            {"id": detection_id, "site_id": site_id},
        ).mappings().fetchone()
    return _row_to_detection(row) if row else None


def set_detection_human_label(detection_id: int, site_id: int, label: str | None) -> Detection | None:
    """Sets (or, with label=None, clears) an operator's ground-truth label
    for one detection -- see app.models.Detection.human_label. Returns
    None (no-op) if the detection doesn't exist in this site, the same
    "not found" contract as get_detection.
    """
    with db_session() as conn:
        row = conn.execute(
            text(
                "UPDATE detection SET human_label = :label WHERE id = :id AND site_id = :site_id "
                "RETURNING *"
            ),
            {"label": label, "id": detection_id, "site_id": site_id},
        ).mappings().fetchone()
    return _row_to_detection(row) if row else None


def list_labeled_detections(site_id: int) -> list[Detection]:
    """Every detection an operator has assigned a human_label to, for
    GET /api/ml/training-data/export -- the CSV app.ml.train actually
    trains from is built out of these.
    """
    with db_session() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM detection WHERE site_id = :site_id AND human_label IS NOT NULL "
                "ORDER BY timestamp"
            ),
            {"site_id": site_id},
        ).mappings().all()
    return [_row_to_detection(row) for row in rows]


def list_detections(
    site_id: int, track_id: int | None = None, limit: int | None = None, offset: int = 0
) -> list[Detection]:
    with db_session() as conn:
        if track_id is not None:
            query = "SELECT * FROM detection WHERE site_id = :site_id AND track_id = :track_id ORDER BY timestamp"
            params: dict = {"site_id": site_id, "track_id": track_id}
        else:
            query = "SELECT * FROM detection WHERE site_id = :site_id ORDER BY timestamp"
            params = {"site_id": site_id}
        if limit is not None:
            query += " LIMIT :limit OFFSET :offset"
            params.update(limit=limit, offset=offset)
        rows = conn.execute(text(query), params).mappings().all()
    return [_row_to_detection(row) for row in rows]


def list_recent_detections(track_id: int, site_id: int, limit: int) -> list[Detection]:
    """Most recent `limit` detections for a track, for classification
    fusion (app/fusion.py) -- order doesn't matter to a weighted vote, only
    which detections are included.
    """
    with db_session() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM detection WHERE track_id = :track_id AND site_id = :site_id "
                "ORDER BY timestamp DESC LIMIT :limit"
            ),
            {"track_id": track_id, "site_id": site_id, "limit": limit},
        ).mappings().all()
    return [_row_to_detection(row) for row in rows]


def purge_old_detections(before: datetime) -> int:
    """Delete detections older than `before`. Returns the number removed."""
    with db_session() as conn:
        result = conn.execute(
            text("DELETE FROM detection WHERE timestamp < :before"), {"before": before.isoformat()}
        )
    return result.rowcount


def purge_old_tracks(before: datetime) -> int:
    """Delete tracks that finished (status != 'active') before `before`.
    Returns the number removed.

    An active track is never purged regardless of age -- last_seen only
    moves forward while a track keeps updating, so "active and old" just
    means "long-lived", not "stale". track_kalman_state cascades via its
    own FK-shaped delete first (SQLite doesn't enforce FKs by default, and
    even on PostgreSQL this table declares no ON DELETE behavior, so a
    leftover orphan row would otherwise survive the track it belongs to).
    detection.track_id and incident.track_id are detached (set NULL)
    rather than deleted -- detection/incident retention are separate,
    independent policies (see purge_old_detections and
    DRONE_AUDIT_LOG_RETENTION_DAYS), and an incident's own record of what
    happened shouldn't disappear just because the track it pointed at
    aged out.
    """
    with db_session() as conn:
        track_ids = [
            row[0]
            for row in conn.execute(
                text("SELECT id FROM track WHERE status != 'active' AND last_seen < :before"),
                {"before": before.isoformat()},
            ).all()
        ]
        if not track_ids:
            return 0
        conn.execute(
            text("DELETE FROM track_kalman_state WHERE track_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": track_ids},
        )
        conn.execute(
            text("UPDATE detection SET track_id = NULL WHERE track_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": track_ids},
        )
        conn.execute(
            text("UPDATE incident SET track_id = NULL WHERE track_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": track_ids},
        )
        result = conn.execute(
            text("DELETE FROM track WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": track_ids},
        )
    return result.rowcount


def purge_old_audit_log(before: datetime) -> int:
    """Delete audit log entries older than `before`. Returns the number
    removed. A separate knob from detection/track retention
    (DRONE_AUDIT_LOG_RETENTION_DAYS) -- audit trail compliance requirements
    commonly call for a longer (or indefinite) retention window than raw
    sensor data needs.
    """
    with db_session() as conn:
        result = conn.execute(
            text("DELETE FROM audit_log WHERE occurred_at < :before"), {"before": before.isoformat()}
        )
    return result.rowcount


def _row_to_detection(row) -> Detection:
    return Detection(
        id=row["id"],
        site_id=row["site_id"],
        sensor_id=row["sensor_id"],
        sensor_type=row["sensor_type"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        track_id=row["track_id"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        altitude_m=row["altitude_m"],
        azimuth_deg=row["azimuth_deg"],
        range_m=row["range_m"],
        confidence=row["confidence"],
        raw_data=json.loads(row["raw_data"]) if row["raw_data"] else None,
        georeferenced=bool(row["georeferenced"]),
        human_label=row["human_label"],
    )


# --- Track helpers -----------------------------------------------------

def create_track(track: Track) -> Track:
    if track.site_id is None:
        raise ValueError("create_track requires track.site_id to be set")
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO track
                    (site_id, track_uid, first_seen, last_seen, status, classification,
                     latitude, longitude, altitude_m, heading_deg, speed_mps,
                     position_uncertainty_m, maneuver_probability, aircraft_category)
                VALUES (:site_id, :track_uid, :first_seen, :last_seen, :status, :classification,
                        :latitude, :longitude, :altitude_m, :heading_deg, :speed_mps,
                        :position_uncertainty_m, :maneuver_probability, :aircraft_category)
                RETURNING id
                """
            ),
            {
                "site_id": track.site_id,
                "track_uid": track.track_uid,
                "first_seen": track.first_seen.isoformat(),
                "last_seen": track.last_seen.isoformat(),
                "status": track.status.value,
                "classification": track.classification.value,
                "latitude": track.latitude,
                "longitude": track.longitude,
                "altitude_m": track.altitude_m,
                "heading_deg": track.heading_deg,
                "speed_mps": track.speed_mps,
                "position_uncertainty_m": track.position_uncertainty_m,
                "maneuver_probability": track.maneuver_probability,
                "aircraft_category": track.aircraft_category,
            },
        ).one()
        track.id = row.id
    return track


def update_track(track: Track) -> Track:
    with db_session() as conn:
        conn.execute(
            text(
                """
                UPDATE track
                SET last_seen = :last_seen, status = :status, classification = :classification,
                    latitude = :latitude, longitude = :longitude, altitude_m = :altitude_m,
                    heading_deg = :heading_deg, speed_mps = :speed_mps,
                    position_uncertainty_m = :position_uncertainty_m,
                    maneuver_probability = :maneuver_probability, aircraft_category = :aircraft_category
                WHERE id = :id
                """
            ),
            {
                "last_seen": track.last_seen.isoformat(),
                "status": track.status.value,
                "classification": track.classification.value,
                "latitude": track.latitude,
                "longitude": track.longitude,
                "altitude_m": track.altitude_m,
                "heading_deg": track.heading_deg,
                "speed_mps": track.speed_mps,
                "position_uncertainty_m": track.position_uncertainty_m,
                "maneuver_probability": track.maneuver_probability,
                "aircraft_category": track.aircraft_category,
                "id": track.id,
            },
        )
    return track


def get_track(track_id: int, site_id: int) -> Track | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM track WHERE id = :id AND site_id = :site_id"),
            {"id": track_id, "site_id": site_id},
        ).mappings().fetchone()
    return _row_to_track(row) if row else None


def list_tracks(
    site_id: int, status: str | None = None, limit: int | None = None, offset: int = 0
) -> list[Track]:
    with db_session() as conn:
        if status is not None:
            query = "SELECT * FROM track WHERE site_id = :site_id AND status = :status ORDER BY last_seen DESC"
            params: dict = {"site_id": site_id, "status": status}
        else:
            query = "SELECT * FROM track WHERE site_id = :site_id ORDER BY last_seen DESC"
            params = {"site_id": site_id}
        if limit is not None:
            query += " LIMIT :limit OFFSET :offset"
            params.update(limit=limit, offset=offset)
        rows = conn.execute(text(query), params).mappings().all()
    return [_row_to_track(row) for row in rows]


def _row_to_track(row) -> Track:
    return Track(
        id=row["id"],
        site_id=row["site_id"],
        track_uid=row["track_uid"],
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
        status=row["status"],
        classification=row["classification"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        altitude_m=row["altitude_m"],
        heading_deg=row["heading_deg"],
        speed_mps=row["speed_mps"],
        position_uncertainty_m=row["position_uncertainty_m"],
        maneuver_probability=row["maneuver_probability"],
        aircraft_category=row["aircraft_category"],
    )


# --- IMM filter state helpers -----------------------------------------

class KalmanStateRecord:
    """Persisted IMM filter state for one track: the local tangent-plane
    reference point, each mode's state vector + covariance, and the mode
    probabilities. See app/imm.py.
    """

    def __init__(
        self,
        track_id: int,
        ref_lat: float,
        ref_lon: float,
        models: list[dict],
        mode_probabilities: list[float],
        updated_at: datetime,
    ) -> None:
        self.track_id = track_id
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon
        self.models = models
        self.mode_probabilities = mode_probabilities
        self.updated_at = updated_at


def upsert_kalman_state(record: KalmanStateRecord) -> None:
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO track_kalman_state
                    (track_id, ref_lat, ref_lon, models, mode_probabilities, updated_at)
                VALUES (:track_id, :ref_lat, :ref_lon, :models, :mode_probabilities, :updated_at)
                ON CONFLICT (track_id) DO UPDATE SET
                    models = excluded.models, mode_probabilities = excluded.mode_probabilities,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "track_id": record.track_id,
                "ref_lat": record.ref_lat,
                "ref_lon": record.ref_lon,
                "models": json.dumps(record.models),
                "mode_probabilities": json.dumps(record.mode_probabilities),
                "updated_at": record.updated_at.isoformat(),
            },
        )


def get_kalman_state(track_id: int) -> KalmanStateRecord | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM track_kalman_state WHERE track_id = :track_id"),
            {"track_id": track_id},
        ).mappings().fetchone()
    if row is None:
        return None
    return KalmanStateRecord(
        track_id=row["track_id"],
        ref_lat=row["ref_lat"],
        ref_lon=row["ref_lon"],
        models=json.loads(row["models"]),
        mode_probabilities=json.loads(row["mode_probabilities"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# --- Incident helpers -----------------------------------------------------

def create_incident(incident: Incident) -> Incident:
    if incident.site_id is None:
        raise ValueError("create_incident requires incident.site_id to be set")
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO incident
                    (site_id, incident_uid, incident_type, severity, status, track_id, zone_id,
                     related_track_id, opened_at, closed_at, description, acknowledged_by)
                VALUES (:site_id, :incident_uid, :incident_type, :severity, :status, :track_id, :zone_id,
                        :related_track_id, :opened_at, :closed_at, :description, :acknowledged_by)
                RETURNING id
                """
            ),
            {
                "site_id": incident.site_id,
                "incident_uid": incident.incident_uid,
                "incident_type": incident.incident_type.value,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "track_id": incident.track_id,
                "zone_id": incident.zone_id,
                "related_track_id": incident.related_track_id,
                "opened_at": incident.opened_at.isoformat(),
                "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
                "description": incident.description,
                "acknowledged_by": incident.acknowledged_by,
            },
        ).one()
        incident.id = row.id
    return incident


def get_incident(incident_id: int, site_id: int) -> Incident | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM incident WHERE id = :id AND site_id = :site_id"),
            {"id": incident_id, "site_id": site_id},
        ).mappings().fetchone()
    return _row_to_incident(row) if row else None


def update_incident(incident: Incident) -> Incident:
    with db_session() as conn:
        conn.execute(
            text(
                """
                UPDATE incident
                SET status = :status, closed_at = :closed_at, description = :description,
                    acknowledged_by = :acknowledged_by
                WHERE id = :id
                """
            ),
            {
                "status": incident.status.value,
                "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
                "description": incident.description,
                "acknowledged_by": incident.acknowledged_by,
                "id": incident.id,
            },
        )
    return incident


def get_open_incident(track_id: int, zone_id: int, incident_type: str, site_id: int) -> Incident | None:
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                SELECT * FROM incident
                WHERE track_id = :track_id AND zone_id = :zone_id AND incident_type = :incident_type
                    AND site_id = :site_id AND status != 'resolved'
                ORDER BY opened_at DESC LIMIT 1
                """
            ),
            {"track_id": track_id, "zone_id": zone_id, "incident_type": incident_type, "site_id": site_id},
        ).mappings().fetchone()
    return _row_to_incident(row) if row else None


def get_open_behavioral_incident(
    track_id: int, incident_type: str, site_id: int, related_track_id: int | None = None
) -> Incident | None:
    """Like get_open_incident, but for a behavioral incident (loitering,
    formation, shadowing -- app/behavior.py) that isn't tied to any zone.
    `zone_id = :zone_id` in the zone-based query would never match a NULL
    zone_id (SQL NULL comparison), so this uses IS NULL instead of
    reusing that query with zone_id=None.

    `related_track_id`, when given (SHADOWING only -- see Incident's
    docstring for that field), narrows the dedup check to that specific
    pair: without it, a track already shadowing one other track would
    never get a second incident for shadowing a *different* track at the
    same time, since the first open incident alone would satisfy this
    lookup for either pair.
    """
    query = """
        SELECT * FROM incident
        WHERE track_id = :track_id AND zone_id IS NULL AND incident_type = :incident_type
            AND site_id = :site_id AND status != 'resolved'
    """
    params: dict = {"track_id": track_id, "incident_type": incident_type, "site_id": site_id}
    if related_track_id is None:
        query += " AND related_track_id IS NULL"
    else:
        query += " AND related_track_id = :related_track_id"
        params["related_track_id"] = related_track_id
    query += " ORDER BY opened_at DESC LIMIT 1"

    with db_session() as conn:
        row = conn.execute(text(query), params).mappings().fetchone()
    return _row_to_incident(row) if row else None


def list_open_incidents_for_track(
    track_id: int, site_id: int, incident_type: str | None = None
) -> list[Incident]:
    """Every still-open (open or acknowledged) incident for one track --
    unlike get_open_incident/get_open_behavioral_incident, which each look
    up at most one specific (track, zone/pair, type) combination to dedup
    against before opening a new incident, this is app/incidents.py's
    auto-close path: "what does this track currently have open, so I can
    check whether each one's trigger condition still holds."
    """
    query = "SELECT * FROM incident WHERE track_id = :track_id AND site_id = :site_id AND status != 'resolved'"
    params: dict = {"track_id": track_id, "site_id": site_id}
    if incident_type is not None:
        query += " AND incident_type = :incident_type"
        params["incident_type"] = incident_type
    with db_session() as conn:
        rows = conn.execute(text(query), params).mappings().all()
    return [_row_to_incident(row) for row in rows]


def list_incidents(
    site_id: int, status: str | None = None, limit: int | None = None, offset: int = 0
) -> list[Incident]:
    with db_session() as conn:
        if status is not None:
            query = "SELECT * FROM incident WHERE site_id = :site_id AND status = :status ORDER BY opened_at DESC"
            params: dict = {"site_id": site_id, "status": status}
        else:
            query = "SELECT * FROM incident WHERE site_id = :site_id ORDER BY opened_at DESC"
            params = {"site_id": site_id}
        if limit is not None:
            query += " LIMIT :limit OFFSET :offset"
            params.update(limit=limit, offset=offset)
        rows = conn.execute(text(query), params).mappings().all()
    return [_row_to_incident(row) for row in rows]


def list_incidents_in_range(start: datetime, end: datetime, site_id: int) -> list[Incident]:
    """All incidents opened in [start, end) -- for app/reporting.py's
    compliance/analytics rollups, which need a bounded window rather than
    list_incidents' "most recent N" pagination.
    """
    with db_session() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM incident WHERE opened_at >= :start AND opened_at < :end "
                "AND site_id = :site_id ORDER BY opened_at"
            ),
            {"start": start.isoformat(), "end": end.isoformat(), "site_id": site_id},
        ).mappings().all()
    return [_row_to_incident(row) for row in rows]


def _row_to_incident(row) -> Incident:
    return Incident(
        id=row["id"],
        site_id=row["site_id"],
        incident_uid=row["incident_uid"],
        incident_type=row["incident_type"],
        severity=row["severity"],
        status=row["status"],
        track_id=row["track_id"],
        zone_id=row["zone_id"],
        related_track_id=row["related_track_id"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
        description=row["description"],
        acknowledged_by=row["acknowledged_by"],
    )


# --- Zone helpers -----------------------------------------------------

def create_zone(zone: Zone) -> Zone:
    if zone.site_id is None:
        raise ValueError("create_zone requires zone.site_id to be set")
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO zone (site_id, name, zone_type, polygon, min_altitude_m, max_altitude_m, active)
                VALUES (:site_id, :name, :zone_type, :polygon, :min_altitude_m, :max_altitude_m, :active)
                RETURNING id
                """
            ),
            {
                "site_id": zone.site_id,
                "name": zone.name,
                "zone_type": zone.zone_type.value,
                "polygon": json.dumps(zone.polygon),
                "min_altitude_m": zone.min_altitude_m,
                "max_altitude_m": zone.max_altitude_m,
                "active": int(zone.active),
            },
        ).one()
        zone.id = row.id
    return zone


def update_zone(zone: Zone) -> Zone:
    """zone.id and zone.site_id must already be set (see
    app/api/zones.py's edit_zone -- it fetches the existing row by both
    first, the same 404-not-403-on-a-different-site's-id pattern every
    other site-scoped update in this app follows, so this never needs to
    guard against updating a different site's zone itself).
    """
    if zone.id is None or zone.site_id is None:
        raise ValueError("update_zone requires zone.id and zone.site_id to be set")
    with db_session() as conn:
        conn.execute(
            text(
                """
                UPDATE zone SET
                    name = :name, zone_type = :zone_type, polygon = :polygon,
                    min_altitude_m = :min_altitude_m, max_altitude_m = :max_altitude_m, active = :active
                WHERE id = :id AND site_id = :site_id
                """
            ),
            {
                "id": zone.id,
                "site_id": zone.site_id,
                "name": zone.name,
                "zone_type": zone.zone_type.value,
                "polygon": json.dumps(zone.polygon),
                "min_altitude_m": zone.min_altitude_m,
                "max_altitude_m": zone.max_altitude_m,
                "active": int(zone.active),
            },
        )
    return zone


def get_zone(zone_id: int, site_id: int) -> Zone | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM zone WHERE id = :id AND site_id = :site_id"),
            {"id": zone_id, "site_id": site_id},
        ).mappings().fetchone()
    return _row_to_zone(row) if row else None


def get_zone_by_name(name: str, site_id: int) -> Zone | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM zone WHERE name = :name AND site_id = :site_id"),
            {"name": name, "site_id": site_id},
        ).mappings().fetchone()
    return _row_to_zone(row) if row else None


def list_zones(site_id: int, active_only: bool = False) -> list[Zone]:
    with db_session() as conn:
        if active_only:
            rows = conn.execute(
                text("SELECT * FROM zone WHERE site_id = :site_id AND active = 1 ORDER BY name"),
                {"site_id": site_id},
            ).mappings().all()
        else:
            rows = conn.execute(
                text("SELECT * FROM zone WHERE site_id = :site_id ORDER BY name"), {"site_id": site_id}
            ).mappings().all()
    return [_row_to_zone(row) for row in rows]


def _row_to_zone(row) -> Zone:
    return Zone(
        id=row["id"],
        site_id=row["site_id"],
        name=row["name"],
        zone_type=row["zone_type"],
        polygon=json.loads(row["polygon"]),
        min_altitude_m=row["min_altitude_m"],
        max_altitude_m=row["max_altitude_m"],
        active=bool(row["active"]),
    )


# --- Sensor registry helpers -------------------------------------------

def upsert_sensor_registration(
    sensor_id: str,
    site_id: int,
    sensor_type: str,
    latitude: float,
    longitude: float,
    altitude_m: float | None,
    azimuth_reference_deg: float,
    active: bool = True,
) -> None:
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO sensor_registry
                    (sensor_id, site_id, sensor_type, latitude, longitude, altitude_m,
                     azimuth_reference_deg, active)
                VALUES (:sensor_id, :site_id, :sensor_type, :latitude, :longitude, :altitude_m,
                        :azimuth_reference_deg, :active)
                ON CONFLICT (sensor_id) DO UPDATE SET
                    site_id = excluded.site_id, sensor_type = excluded.sensor_type,
                    latitude = excluded.latitude, longitude = excluded.longitude,
                    altitude_m = excluded.altitude_m,
                    azimuth_reference_deg = excluded.azimuth_reference_deg,
                    active = excluded.active
                """
            ),
            {
                "sensor_id": sensor_id,
                "site_id": site_id,
                "sensor_type": sensor_type,
                "latitude": latitude,
                "longitude": longitude,
                "altitude_m": altitude_m,
                "azimuth_reference_deg": azimuth_reference_deg,
                "active": int(active),
            },
        )


def get_sensor_registration(sensor_id: str, site_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM sensor_registry WHERE sensor_id = :sensor_id "
                "AND site_id = :site_id AND active = 1"
            ),
            {"sensor_id": sensor_id, "site_id": site_id},
        ).mappings().fetchone()
    return dict(row) if row else None


def list_sensor_registrations(site_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            text("SELECT * FROM sensor_registry WHERE site_id = :site_id ORDER BY sensor_id"),
            {"site_id": site_id},
        ).mappings().all()
    return [dict(row) for row in rows]


# --- Authorized operator (friendly allowlist) helpers -----------------

def upsert_authorized_operator(
    operator_id: str, site_id: int, name: str, public_key: str | None = None, active: bool = True
) -> None:
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO authorized_operator (operator_id, site_id, name, public_key, active)
                VALUES (:operator_id, :site_id, :name, :public_key, :active)
                ON CONFLICT (operator_id) DO UPDATE SET
                    site_id = excluded.site_id, name = excluded.name,
                    public_key = excluded.public_key, active = excluded.active
                """
            ),
            {
                "operator_id": operator_id,
                "site_id": site_id,
                "name": name,
                "public_key": public_key,
                "active": int(active),
            },
        )


def get_authorized_operator(operator_id: str, site_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM authorized_operator WHERE operator_id = :operator_id "
                "AND site_id = :site_id AND active = 1"
            ),
            {"operator_id": operator_id, "site_id": site_id},
        ).mappings().fetchone()
    return dict(row) if row else None


def list_authorized_operators(site_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            text("SELECT * FROM authorized_operator WHERE site_id = :site_id ORDER BY operator_id"),
            {"site_id": site_id},
        ).mappings().all()
    return [dict(row) for row in rows]
