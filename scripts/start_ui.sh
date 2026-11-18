#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

exec streamlit run frontend/streamlit_app.py \
  --server.address "${UI_HOST:-127.0.0.1}" \
  --server.port "${UI_PORT:-8501}" \
  --server.headless true
