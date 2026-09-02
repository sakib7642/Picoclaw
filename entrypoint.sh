#!/bin/sh
set -eu

PORT="${PORT:-8080}"
MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
MODEL_PATH="/app/models/Qwen3.5-0.8B-IQ2_M.gguf"
PICO_HOME="/root/.picoclaw"
CONFIG_SOURCE="/config/config.json"
CONFIG_PATH="${PICO_HOME}/config.json"

printf '%s\n' '=================================================='
printf '%s\n' ' PicoClaw WebUI + local Qwen3.5-0.8B stack'
printf ' UTC: %s\n' "$(date -u)"
printf ' Public WebUI port: %s\n' "${PORT}"
printf ' Local model API: %s:%s\n' "${MODEL_HOST}" "${MODEL_PORT}"
printf '%s\n' '=================================================='

mkdir -p "${PICO_HOME}/workspace" "${PICO_HOME}/logs"

# Seed the config only once. WebUI changes are therefore not overwritten
# when the official launcher restarts the gateway.
if [ ! -f "${CONFIG_PATH}" ] && [ -f "${CONFIG_SOURCE}" ]; then
    cp "${CONFIG_SOURCE}" "${CONFIG_PATH}"
fi

export PICOCLAW_BINARY="/app/picoclaw"

printf '%s\n' '--- binaries ---'
ls -lh /app/picoclaw /app/picoclaw-launcher /app/llama-server
printf '%s\n' '--- model ---'
ls -lh "${MODEL_PATH}"
printf '%s\n' '--- PicoClaw version ---'
/app/picoclaw version || true
printf '%s\n' '--- llama.cpp version ---'
/app/llama-server --version || true

# The model server is deliberately independent from PicoClaw. A WebUI
# gateway restart cannot kill the model process.
/app/scripts/model_supervisor.sh &
MODEL_SUPERVISOR_PID=$!

cleanup() {
    kill "${MODEL_SUPERVISOR_PID}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Wait until the model server has actually loaded the GGUF.
i=0
while ! curl -fsS "http://${MODEL_HOST}:${MODEL_PORT}/health" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge 240 ]; then
        echo 'ERROR: Qwen3.5 model server did not become healthy within 240s.' >&2
        exit 1
    fi
    sleep 1
done

echo 'Qwen3.5 model server: healthy'
echo 'Starting official PicoClaw WebUI launcher...'

# Keep the gateway/WebUI process supervised. If the launcher exits after a
# WebUI-triggered restart, restart it inside the same container instead of
# letting PID 1 die and taking the Render service down.
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
