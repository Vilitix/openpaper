#!/usr/bin/env bash
set -euo pipefail
AGENT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$AGENT_DIR"

SECRET="$(grep '^LOCAL_AUTH_SECRET=' .env | cut -d= -f2-)"
PORT="${OPENPAPER_SERVER_PORT:-8100}"
HOST="${OPENPAPER_HOST:-192.168.1.20}"

echo "Local login (open in the browser):"
echo "  http://localhost:${PORT}/api/auth/local?secret=${SECRET}"
echo "  http://${HOST}:${PORT}/api/auth/local?secret=${SECRET}"
echo
echo "This link sets the session cookie and redirects to the app."
