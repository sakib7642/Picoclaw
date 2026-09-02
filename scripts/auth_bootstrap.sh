#!/bin/sh
set -eu
PORT="${PORT:-8080}"
PASSWORD="${PICOCLAW_WEBUI_PASSWORD:-}"
[ -n "$PASSWORD" ] || exit 0

# Initialize the official launcher's bcrypt dashboard password only when the
# password store is empty. A later WebUI password change is never overwritten.
while :; do
    STATUS="$(curl -fsS "http://127.0.0.1:${PORT}/api/auth/status" 2>/dev/null || true)"
    case "$STATUS" in
      *'"initialized":true'*)
        echo '[WebUI-Auth] password store already initialized'
        exit 0
        ;;
      *'"initialized":false'*)
        BODY="$(python3 - "$PASSWORD" <<'PY'
import json, sys
p = sys.argv[1]
print(json.dumps({'password': p, 'confirm': p}))
PY
)"
        if curl -fsS -X POST "http://127.0.0.1:${PORT}/api/auth/setup" \
            -H 'Content-Type: application/json' \
            --data "$BODY" >/dev/null 2>&1; then
            echo '[WebUI-Auth] dashboard password initialized from PICOCLAW_WEBUI_PASSWORD'
            exit 0
        fi
        ;;
    esac
    sleep 2
done
