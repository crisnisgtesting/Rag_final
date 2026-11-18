#!/usr/bin/env bash
set -euo pipefail

HOST="${API_HOST:-127.0.0.1}"
PORT="${API_PORT:-8000}"
URL="http://${HOST}:${PORT}/health/ready"

curl --fail --silent --show-error --max-time 10 "$URL"
echo
