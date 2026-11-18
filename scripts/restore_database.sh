#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

: "${DATABASE_URL:?DATABASE_URL must be set}"

if [ $# -ne 1 ]; then
  echo "Usage: $0 /path/to/backup.dump" >&2
  exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

read -r -p "This restores into $DATABASE_URL and overwrites existing objects. Continue? [y/N] " reply
if [ "$reply" != "y" ] && [ "$reply" != "Y" ]; then
  echo "Aborted."
  exit 1
fi

pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DATABASE_URL" "$BACKUP_FILE"
echo "Restore complete."
