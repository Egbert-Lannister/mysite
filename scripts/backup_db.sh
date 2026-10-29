#!/usr/bin/env bash
# Online SQLite backup, verified before retaining the latest 14 copies.
# Configure DB, BACKUP_DIR, or PYTHON for a different deployment layout.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
DB="${DB:-$PROJECT_DIR/db.sqlite3}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
if [[ -z "${PYTHON:-}" ]]; then
    if [[ -x "$PROJECT_DIR/venv/bin/python" ]]; then
        PYTHON="$PROJECT_DIR/venv/bin/python"
    else
        PYTHON="python3"
    fi
fi
# Python's standard sqlite3 backup API includes WAL commits; never use raw cp.
exec "$PYTHON" "$SCRIPT_DIR/backup_db.py" --database "$DB" --output "$BACKUP_DIR" --keep 14
