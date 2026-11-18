#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.11 was not found. Set PYTHON_BIN or install python3.11." >&2
  exit 1
fi

if [ ! -d "venv" ]; then
  echo "Creating virtual environment in ./venv"
  "$PYTHON_BIN" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip

if [ "${1:-}" = "--dev" ]; then
  python -m pip install -r requirements-dev.txt
else
  python -m pip install -r requirements.txt
fi

echo "Dependencies installed. Activate with: source venv/bin/activate"
