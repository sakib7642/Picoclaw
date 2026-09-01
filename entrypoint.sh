#!/bin/sh
set -eu

PORT="${PORT:-8080}"
PICOLM_HOST="${PICOLM_SERVER_HOST:-127.0.0.1}"
PICOLM_PORT="${PICOLM_SERVER_PORT:-8000}"
PICO_HOME="/root/.picoclaw"
CONFIG_SOURCE="/config/config.json"
CONFIG_PATH="${PICO_HOME}/config.json"

printf '%s\n' '=================================================='
printf '%s\n' ' PicoClaw WebUI + local PicoLM stack'
printf ' UTC: %s\n' "$(date -u)"
printf ' Public WebUI port: %s\n' "${PORT}"
printf '%s\n' '=================================================='

mkdir -p "${PICO_HOME}/workspace" "${PICO_HOME}/logs"

# Seed the user-editable PicoClaw config on first boot / fresh container.
# The WebUI remains the source of truth after startup.
if [ -f "${CONFIG_SOURCE}" ]; then
    cp "${CONFIG_SOURCE}" "${CONFIG_PATH}"
fi

# The launcher needs to know exactly which gateway binary it should spawn.
export PICOCLAW_BINARY="/app/picoclaw"

printf '%s\n' '--- binaries ---'
ls -lh /app/picoclaw /app/picoclaw-launcher /app/picolm
printf '%s\n' '--- model ---'
ls -lh /app/models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf
printf '%s\n' '--- PicoClaw version ---'
/app/picoclaw version || true

# Start the local OpenAI-compatible adapter. It is bound to loopback only.
python3 /app/scripts/picolm_server.py &
ADAPTER_PID=$!

cleanup() {
    kill "${ADAPTER_PID}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Wait for the adapter instead of relying on a fixed sleep.
i=0
while ! curl -fsS "http://${PICOLM_HOST}:${PICOLM_PORT}/health" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge 60 ]; then
        echo 'ERROR: PicoLM adapter did not become healthy.' >&2
        exit 1
    fi
    sleep 1
done

echo 'PicoLM adapter: healthy'
echo 'Starting official PicoClaw WebUI launcher...'

# The launcher owns the gateway lifecycle. The gateway itself stays on the
# internal 127.0.0.1:18790 from config.json; only the WebUI is public.
exec /app/picoclaw-launcher \
    -host 0.0.0.0 \
    -port "${PORT}" \
    -public \
    -no-browser \
    "${CONFIG_PATH}"
