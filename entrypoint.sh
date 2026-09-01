#!/bin/sh
set -e

echo "=================================================="
echo " Starting PicoClaw + PicoLM Stack on Render"
echo " Timestamp: $(date -u)"
echo "=================================================="

# Determine Render / Default Port
PORT="${PORT:-8080}"
export PICOCLAW_GATEWAY_PORT="${PORT}"
export PICOCLAW_GATEWAY_HOST="0.0.0.0"

echo "[Init] Configured Gateway Port: ${PORT}"
echo "[Init] Configured Gateway Host: 0.0.0.0"

# Setup directories
mkdir -p /root/.picoclaw/workspace
mkdir -p /root/.picoclaw/logs
mkdir -p /config

# Setup configuration
if [ -f "/config/config.json" ]; then
    echo "[Init] Copying /config/config.json to /root/.picoclaw/config.json"
    cp /config/config.json /root/.picoclaw/config.json
elif [ -f "/app/config/config.json" ]; then
    echo "[Init] Copying /app/config/config.json to /root/.picoclaw/config.json"
    cp /app/config/config.json /root/.picoclaw/config.json
fi

# Remove stale PID if container was restarted
rm -f /root/.picoclaw/.picoclaw.pid

# Diagnostics for binaries and model
echo "[Init] Checking binaries and model files..."
ls -lh /app/picoclaw /app/picolm /app/models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf || true

# Start PicoLM OpenAI Adapter in background
echo "[Init] Starting PicoLM adapter server on 127.0.0.1:8000..."
python3 /app/scripts/picolm_server.py &
ADAPTER_PID=$!

# Trap signals for clean exit
cleanup() {
    echo "[Exit] Stopping adapter process (PID $ADAPTER_PID)..."
    kill -TERM "$ADAPTER_PID" 2>/dev/null || true
    wait "$ADAPTER_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

# Wait for local adapter to become ready
echo "[Init] Waiting for local adapter health check (127.0.0.1:8000/v1/models)..."
MAX_RETRIES=30
COUNT=0
HEALTHY=0

while [ "$COUNT" -lt "$MAX_RETRIES" ]; do
    if curl -sf http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
        echo "[Init] Local PicoLM adapter is healthy and ready!"
        HEALTHY=1
        break
    fi
    sleep 1
    COUNT=$((COUNT + 1))
done

if [ "$HEALTHY" -ne 1 ]; then
    echo "[FATAL] Local PicoLM adapter failed to start within ${MAX_RETRIES} seconds."
    kill -9 "$ADAPTER_PID" 2>/dev/null || true
    exit 1
fi

echo "[Init] Starting PicoClaw Gateway on 0.0.0.0:${PORT}..."
echo "[Init] PicoClaw Version: $(/app/picoclaw version 2>&1 || true)"

# Launch PicoClaw gateway as foreground process
exec /app/picoclaw gateway --host 0.0.0.0