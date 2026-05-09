#!/usr/bin/env bash
# Daily SQLite backup. Keeps last 14 days.
# Usage:
#   ./scripts/backup_db.sh
# Cron entry (daily at 03:00):
#   0 3 * * * /root/mysite/scripts/backup_db.sh >> /root/mysite/backups/backup.log 2>&1

set -euo pipefail

DB="/root/mysite/db.sqlite3"
BACKUP_DIR="/root/mysite/backups"
TS=$(date +%Y%m%d_%H%M%S)
DEST="$BACKUP_DIR/db.sqlite3.$TS"

mkdir -p "$BACKUP_DIR"

# Use sqlite3 .backup for hot online backup (handles WAL/transactions safely).
# Falls back to cp if sqlite3 not available.
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB" ".backup '$DEST'"
else
    cp "$DB" "$DEST"
fi

# Verify the backup is non-empty (refuse to keep an empty 0-byte file).
if [ ! -s "$DEST" ]; then
    echo "[$(date)] BACKUP FAILED: $DEST is empty, removing." >&2
    rm -f "$DEST"
    exit 1
fi

echo "[$(date)] Backup OK: $DEST ($(du -h "$DEST" | cut -f1))"

# Retention: keep most recent 14 backups, delete the rest.
ls -1t "$BACKUP_DIR"/db.sqlite3.* 2>/dev/null | tail -n +15 | xargs -r rm -f
