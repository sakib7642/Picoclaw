#!/bin/sh
set -eu

PORT="${PORT:-8080}"
MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
MODEL_PATH="/app/models/Qwen3.5-0.8B.Q4_K_M.gguf"
PICO_HOME="/root/.picoclaw"
CONFIG_SOURCE="/config/config.json"
CONFIG_PATH="${PICO_HOME}/config.json"

printf '%s\n' '=================================================='
printf '%s\n' ' PicoClaw WebUI + local Qwen3.5 stack'
printf ' UTC: %s\n' "$(date -u)"
printf ' Public WebUI port: %s\n' "${PORT}"
printf ' Local model API: %s:%s\n' "${MODEL_HOST}" "${MODEL_PORT}"
printf '%s\n' '=================================================='

mkdir -p "${PICO_HOME}/workspace" "${PICO_HOME}/logs"

# IMPORTANT: never overwrite a WebUI-edited config on restart.
# On a brand-new container there is no config yet, so seed it once.
if [ ! -f "${CONFIG_PATH}" ] && [ -f "${CONFIG_SOURCE}" ]; then
    cp "${CONFIG_SOURCE}" "${CONFIG_PATH}"
fi

# The official launcher uses this binary for its gateway child process.
export PICOCLAW_BINARY="/app/picoclaw"
export LD_LIBRARY_PATH="/opt/llama:${LD_LIBRARY_PATH:-}"

printf '%s\n' '--- binaries ---'
ls -lh /app/picoclaw /app/picoclaw-launcher /app/llama-server
printf '%s\n' '--- model ---'
ls -lh "${MODEL_PATH}"
printf '%s\n' '--- PicoClaw version ---'
/app/picoclaw version || true
printf '%s\n' '--- llama.cpp version ---'
/app/llama-server --version || true

# Keep the model service independent from the PicoClaw gateway. A gateway
# restart from WebUI therefore does not kill the model server.
/app/scripts/model_supervisor.sh &
MODEL_SUPERVISOR_PID=$!

cleanup() {
    kill "${MODEL_SUPERVISOR_PID}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Wait for a fully loaded model, not merely for the process to exist.
i=0
while ! curl -fsS "http://${MODEL_HOST}:${MODEL_PORT}/health" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -ge 180 ]; then
        echo 'ERROR: Qwen3.5 model server did not become healthy within 180s.' >&2
        exit 1
    fi
    sleep 1
done

echo 'Qwen3.5 model server: healthy'
echo 'Starting official PicoClaw WebUI launcher...'

# Gateway remains private on 127.0.0.1:18790. Only the official WebUI is public.
exec /app/picoclaw-launcher \
    -host 0.0.0.0 \
    -port "${PORT}" \
    -public \
    -no-browser \
    "${CONFIG_PATH}"
