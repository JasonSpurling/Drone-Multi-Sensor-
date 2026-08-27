#!/usr/bin/env bash
# Restores a database backup written by scripts/backup.sh. Stop the app
# first (a restore while it's running races live writes) -- this script
# doesn't do that for you, since how you stop it depends on how you
# deployed it (systemctl stop drone-multi-sensor / docker compose stop app
# / Ctrl+C on a foreground process).
#
# Usage:
#   scripts/restore.sh backups/drone_sensor_20260101T000000Z.db
#   scripts/restore.sh backups/drone_sensor_20260101T000000Z.dump
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <backup-file>" >&2
    exit 1
fi

BACKUP_FILE="$1"
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "No such backup file: $BACKUP_FILE" >&2
    exit 1
fi

DB_PATH="${DRONE_DB_PATH:-data/drone_sensor.db}"
DATABASE_URL="${DRONE_DATABASE_URL:-sqlite:///${DB_PATH}}"

case "$BACKUP_FILE" in
    *.db)
        if [[ "$DATABASE_URL" != sqlite:///* ]]; then
            echo "Refusing to restore a SQLite backup ($BACKUP_FILE) over a non-SQLite DATABASE_URL ($DATABASE_URL)." >&2
            exit 1
        fi
        SQLITE_PATH="${DATABASE_URL#sqlite:///}"
        mkdir -p "$(dirname "$SQLITE_PATH")"
        if [[ -f "$SQLITE_PATH" ]]; then
            SAFETY_COPY="${SQLITE_PATH}.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
            echo "Existing database found -- saving it to $SAFETY_COPY before overwriting."
            cp "$SQLITE_PATH" "$SAFETY_COPY"
        fi
        echo "Restoring $BACKUP_FILE -> $SQLITE_PATH"
        cp "$BACKUP_FILE" "$SQLITE_PATH"
        echo "Restore complete. Start the app normally to use it."
        ;;
    *.dump)
        if [[ "$DATABASE_URL" != postgresql* ]]; then
            echo "Refusing to restore a PostgreSQL backup ($BACKUP_FILE) over a non-PostgreSQL DATABASE_URL ($DATABASE_URL)." >&2
            exit 1
        fi
        # See scripts/backup.sh's matching comment: pg_restore only
        # understands plain "postgresql://" URIs, not SQLAlchemy's
        # "postgresql+psycopg2://" form this repo documents DATABASE_URL as.
        PG_URI="${DATABASE_URL/postgresql+*:\/\//postgresql://}"
        echo "Restoring $BACKUP_FILE -> $PG_URI"
        pg_restore -d "$PG_URI" --clean --if-exists "$BACKUP_FILE"
        echo "Restore complete."
        ;;
    *)
        echo "Unrecognized backup file extension (expected .db or .dump): $BACKUP_FILE" >&2
        exit 1
        ;;
esac
