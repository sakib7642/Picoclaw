#!/bin/sh
set -eu

PORT="${PORT:-8080}"
MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
ROUTER_HOST="${ROUTER_HOST:-127.0.0.1}"
ROUTER_PORT="${ROUTER_PORT:-8100}"
MODEL_PATH="/app/models/MobileLLM-350M.Q4_K_M.gguf"
PICO_HOME="/root/.picoclaw"
CONFIG_SOURCE="/config/config.json"
CONFIG_PATH="${PICO_HOME}/config.json"

printf '%s\n' '=================================================='
printf '%s\n' ' PicoClaw WebUI + intelligent multi-provider router'
printf ' UTC: %s\n' "$(date -u)"
printf ' Public WebUI port: %s\n' "${PORT}"
printf ' Router API: %s:%s\n' "${ROUTER_HOST}" "${ROUTER_PORT}"
printf ' Local fallback API: %s:%s\n' "${MODEL_HOST}" "${MODEL_PORT}"
printf '%s\n' '=================================================='

mkdir -p "${PICO_HOME}/workspace" "${PICO_HOME}/logs"

# Seed only once so WebUI edits are not overwritten by a launcher restart.
if [ ! -f "${CONFIG_PATH}" ] && [ -f "${CONFIG_SOURCE}" ]; then
    cp "${CONFIG_SOURCE}" "${CONFIG_PATH}"
fi

export PICOCLAW_BINARY="/app/picoclaw"

printf '%s\n' '--- binaries ---'
ls -lh /app/picoclaw /app/picoclaw-launcher /app/llama-server
printf '%s\n' '--- local fallback model ---'
ls -lh "${MODEL_PATH}"
printf '%s\n' '--- PicoClaw version ---'
/app/picoclaw version || true
printf '%s\n' '--- llama.cpp version ---'
/app/llama-server --version || true

# Local model is only the last-resort fallback. It may fail/OOM on Render Free
# without preventing hosted proxy models and the WebUI from starting.
/app/scripts/model_supervisor.sh &
MODEL_SUPERVISOR_PID=$!

/app/scripts/router_supervisor.sh &
ROUTER_SUPERVISOR_PID=$!

cleanup() {
    kill "${MODEL_SUPERVISOR_PID}" 2>/dev/null || true
    kill "${ROUTER_SUPERVISOR_PID}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Wait for the router, not the local model. Hosted providers can serve requests
# even while the tiny local fallback is still loading or restarting.
i=0
while ! curl -fsS "http://${ROUTER_HOST}:${ROUTER_PORT}/health" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge 120 ]; then
        echo 'ERROR: model router did not become healthy within 120s.' >&2
        exit 1
    fi
    sleep 1
done

echo 'Intelligent model router: healthy'

# Initialize the official WebUI password on a fresh Render filesystem. The
# script exits immediately once the password store already contains a hash.
/app/scripts/auth_bootstrap.sh &

# Keep the official PicoClaw launcher supervised. A WebUI-triggered gateway
# restart therefore stays inside this container instead of killing PID 1.
echo 'Starting official PicoClaw WebUI launcher...'
while :; do
    /app/picoclaw-launcher \
        -host 0.0.0.0 \
        -port "${PORT}" \
        -public \
        -no-browser \
        "${CONFIG_PATH}" || status=$?

    status="${status:-0}"
    echo "[Launcher-Supervisor] launcher exited with code ${status}; restarting in 2s" >&2
    unset status
    sleep 2
done
