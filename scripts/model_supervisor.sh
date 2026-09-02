#!/bin/sh
set -eu

MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
MODEL_PATH="/app/models/SmolLM2-135M-Instruct-Q4_K_M.gguf"
THREADS="${LOCAL_MODEL_THREADS:-1}"
CTX="${LOCAL_MODEL_CONTEXT:-4096}"

while :; do
    echo "[SmolLM2-Supervisor] starting SmolLM2-135M-Instruct on ${MODEL_HOST}:${MODEL_PORT} (ctx=${CTX})"

    if /app/llama-server \
        --model "${MODEL_PATH}" \
        --alias "smollm2-135m" \
        --host "${MODEL_HOST}" \
        --port "${MODEL_PORT}" \
        --jinja \
        --ctx-size "${CTX}" \
        --threads "${THREADS}" \
        --batch-size 4 \
        --ubatch-size 4 \
        --parallel 1 \
        --no-webui \
        --temp 0.4 \
        --top-k 20 \
        --top-p 0.9 \
        --min-p 0.05 \
        --repeat-penalty 1.1; then
        status=0
    else
        status=$?
    fi

    echo "[SmolLM2-Supervisor] llama.cpp exited with code ${status}; restarting in 2s" >&2
    sleep 2
done
