-- SQLite schema for the drone multi-sensor system.

CREATE TABLE IF NOT EXISTS zone (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    zone_type       TEXT NOT NULL,
    polygon         TEXT NOT NULL,      -- JSON list of [lat, lon] pairs
    min_altitude_m  REAL,
    max_altitude_m  REAL,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS track (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_uid       TEXT NOT NULL UNIQUE,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    classification  TEXT NOT NULL DEFAULT 'unknown',
    latitude        REAL,
    longitude       REAL,
    altitude_m      REAL
);

CREATE TABLE IF NOT EXISTS detection (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id       TEXT NOT NULL,
    sensor_type     TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    track_id        INTEGER REFERENCES track (id),
    latitude        REAL,
    longitude       REAL,
    altitude_m      REAL,
    azimuth_deg     REAL,
    range_m         REAL,
    confidence      REAL NOT NULL DEFAULT 1.0,
    raw_data        TEXT            -- JSON blob
);

CREATE TABLE IF NOT EXISTS incident (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_uid    TEXT NOT NULL UNIQUE,
    incident_type   TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'low',
    status          TEXT NOT NULL DEFAULT 'open',
    track_id        INTEGER REFERENCES track (id),
    zone_id         INTEGER REFERENCES zone (id),
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    description     TEXT
);

CREATE INDEX IF NOT EXISTS idx_detection_track_id ON detection (track_id);
CREATE INDEX IF NOT EXISTS idx_detection_timestamp ON detection (timestamp);
CREATE INDEX IF NOT EXISTS idx_incident_track_id ON incident (track_id);
CREATE INDEX IF NOT EXISTS idx_incident_zone_id ON incident (zone_id);
