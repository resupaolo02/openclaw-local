#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="$ROOT_DIR/openclaw-data/openclaw.json"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is required."
  exit 1
fi

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Error: config not found at $CONFIG_PATH"
  exit 1
fi

TOKEN="$(python3 - <<PY
import json
with open(r'''$CONFIG_PATH''', 'r', encoding='utf-8') as f:
    data = json.load(f)
token = data.get('gateway', {}).get('auth', {}).get('token', '')
print(token)
PY
)"

if [ -z "$TOKEN" ]; then
  echo "Error: gateway.auth.token is missing in $CONFIG_PATH"
  exit 1
fi

cd "$ROOT_DIR"

PENDING_JSON="$(docker compose exec -T openclaw openclaw devices list --token "$TOKEN" --json)"

PENDING_COUNT="$(python3 - <<PY
import json
data = json.loads(r'''$PENDING_JSON''')
print(len(data.get('pending', [])))
PY
)"

if [ "$PENDING_COUNT" = "0" ]; then
  echo "No pending device pairing requests."
  exit 0
fi

echo "Found $PENDING_COUNT pending request(s). Approving the most recent one..."
docker compose exec -T openclaw openclaw devices approve --latest --token "$TOKEN" --json
echo "Done."
