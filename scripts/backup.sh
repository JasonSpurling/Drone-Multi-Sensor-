#!/usr/bin/env bash
# Backs up the app's database -- SQLite or PostgreSQL, whichever
# DRONE_DATABASE_URL (or its default) points at -- using each engine's own
# safe-against-a-live-writer backup mechanism (see README.md's "Backup and
# restore" section for why a plain `cp`/file copy of a live SQLite file
# isn't safe). Prunes backups older than the last BACKUP_KEEP_COUNT so this
# is safe to run unattended (cron/systemd timer) without slowly filling the
# disk -- see deploy/drone-multi-sensor-backup.timer for a ready-made
# schedule.
#
# Usage:
#   scripts/backup.sh                       # writes into ./backups
#   BACKUP_DIR=/mnt/backups scripts/backup.sh
#   BACKUP_KEEP_COUNT=30 scripts/backup.sh  # keep more/fewer than the default 14
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-backups}"
BACKUP_KEEP_COUNT="${BACKUP_KEEP_COUNT:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

# Mirrors app/config.py's own DATABASE_URL default exactly, so a deployment
# that never set either env var still gets backed up correctly.
DB_PATH="${DRONE_DB_PATH:-data/drone_sensor.db}"
DATABASE_URL="${DRONE_DATABASE_URL:-sqlite:///${DB_PATH}}"

prune_old_backups() {
    local pattern="$1"
    # ls -t (newest first) + tail -n +$((N+1)) (everything past the Nth)
    # rather than find -mtime, so "keep the last N" is exact regardless of
    # how far apart backups actually ran.
    # shellcheck disable=SC2012
    ls -t "$BACKUP_DIR"/$pattern 2>/dev/null | tail -n "+$((BACKUP_KEEP_COUNT + 1))" | while read -r old; do
        echo "Pruning old backup: $old"
        rm -f -- "$old"
    done
}

if [[ "$DATABASE_URL" == sqlite:///* ]]; then
    SQLITE_PATH="${DATABASE_URL#sqlite:///}"
    if [[ ! -f "$SQLITE_PATH" ]]; then
        echo "No SQLite database at $SQLITE_PATH yet -- nothing to back up." >&2
        exit 0
    fi
    OUT="$BACKUP_DIR/drone_sensor_${TIMESTAMP}.db"
    echo "Backing up SQLite database $SQLITE_PATH -> $OUT"
    # Python's sqlite3.Connection.backup() (not a file copy, and not a
    # dependency on the separate `sqlite3` CLI package, which isn't
    # installed by default in this app's own Docker image or many minimal
    # Linux installs) goes through SQLite's own backup API -- the same one
    # the `sqlite3 ".backup"` CLI command uses -- which is safe against a
    # concurrently-updated WAL/journal, unlike a plain file copy.
    python3 -c "
import sqlite3
src = sqlite3.connect('$SQLITE_PATH')
dst = sqlite3.connect('$OUT')
with dst:
    src.backup(dst)
src.close()
dst.close()
"
    prune_old_backups "drone_sensor_*.db"

elif [[ "$DATABASE_URL" == postgresql* ]]; then
    OUT="$BACKUP_DIR/drone_sensor_${TIMESTAMP}.dump"
    echo "Backing up PostgreSQL database -> $OUT"
    # pg_dump/pg_restore only understand plain "postgresql://" connection
    # URIs -- DRONE_DATABASE_URL is documented (README, .env.example) as
    # SQLAlchemy's own "postgresql+psycopg2://" form, which pg_dump doesn't
    # recognize as a network address at all and silently falls back to a
    # local Unix-socket connection instead of erroring loudly. Strip the
    # "+driver" suffix so this works with the URL exactly as every other
    # doc in this repo tells you to set it.
    PG_URI="${DATABASE_URL/postgresql+*:\/\//postgresql://}"
    pg_dump -Fc "$PG_URI" > "$OUT"
    prune_old_backups "drone_sensor_*.dump"

else
    echo "Unrecognized DATABASE_URL scheme: $DATABASE_URL" >&2
    exit 1
fi

echo "Backup complete: $OUT"
