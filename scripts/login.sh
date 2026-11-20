#!/usr/bin/env bash
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$AGENT_DIR"

EMAIL="${1:-arthur@example.com}"
NAME="${2:-Arthur}"
USER_ID="11111111-1111-4111-8111-111111111111"
SESSION_ID="$(cat /proc/sys/kernel/random/uuid)"
TOKEN="$(openssl rand -hex 32)"
CLIENT_PORT="${OPENPAPER_CLIENT_PORT:-3100}"

docker compose --project-directory "$AGENT_DIR" -f "$AGENT_DIR/docker-compose.yml" exec -T openpaper-postgres psql -U postgres -d annotated-paper -v ON_ERROR_STOP=1 -q <<SQL
INSERT INTO users (id, email, name, is_active, is_admin, is_blocked, auth_provider, provider_user_id, is_email_verified)
VALUES ('$USER_ID', '$EMAIL', '$NAME', true, true, false, 'email', '$EMAIL', true)
ON CONFLICT (id) DO UPDATE SET
  email = EXCLUDED.email,
  name = EXCLUDED.name,
  is_active = true,
  is_admin = true,
  is_blocked = false,
  is_email_verified = true;

DELETE FROM sessions WHERE user_id = '$USER_ID' AND expires_at < now();

INSERT INTO sessions (id, user_id, token, expires_at)
VALUES ('$SESSION_ID', '$USER_ID', '$TOKEN', now() + interval '30 days');
SQL

cat <<EOF
Open Paper: http://localhost:${CLIENT_PORT}

Paste this in the browser console on that page, then reload:

document.cookie = "session_token=${TOKEN}; path=/"; location.reload()

Signed in as: ${EMAIL}
EOF
