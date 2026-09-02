#!/bin/sh
set -eu

PORT="${PORT:-8080}"
MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
ROUTER_HOST="${ROUTER_HOST:-127.0.0.1}"
ROUTER_PORT="${ROUTER_PORT:-8100}"
MODEL_PATH="/app/models/MobileLLM-376M-Q4_K_S.gguf"
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

/app/scripts/model_supervisor.sh &
MODEL_SUPERVISOR_PID=$!

/app/scripts/router_supervisor.sh &
ROUTER_SUPERVISOR_PID=$!

cleanup() {
    kill "${MODEL_SUPERVISOR_PID}" 2>/dev/null || true
    kill "${ROUTER_SUPERVISOR_PID}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Do not expose the public WebUI until the local fallback is genuinely ready.
i=0
while ! curl -fsS "http://${MODEL_HOST}:${MODEL_PORT}/health" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge 240 ]; then
        echo 'ERROR: MobileLLM local fallback did not become healthy within 240s.' >&2
        exit 1
    fi
    sleep 1
done
echo 'MobileLLM local fallback: healthy'

# Wait for the router as well.
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
/app/scripts/auth_bootstrap.sh &

echo 'Starting official PicoClaw WebUI launcher...'
while :; do
    status=0
    /app/picoclaw-launcher \
        -host 0.0.0.0 \
        -port "${PORT}" \
        -public \
        -no-browser \
        "${CONFIG_PATH}" || status=$?

    echo "[Launcher-Supervisor] launcher exited with code ${status}; restarting in 2s" >&2
    sleep 2
done
