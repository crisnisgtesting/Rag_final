#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

: "${DATABASE_URL:?DATABASE_URL must be set}"

BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="$BACKUP_DIR/fmm_${STAMP}.dump"

pg_dump --format=custom --no-owner --no-privileges --file="$TARGET" "$DATABASE_URL"
chmod 600 "$TARGET"
echo "Backup written to $TARGET"
