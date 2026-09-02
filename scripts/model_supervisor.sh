#!/bin/sh
set -eu

MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
MODEL_PATH="/app/models/MobileLLM-376M-Q4_K_M.gguf"
THREADS="${LOCAL_MODEL_THREADS:-1}"
CTX="${LOCAL_MODEL_CONTEXT:-1024}"

while :; do
    echo "[MobileLLM-Supervisor] starting llama.cpp on ${MODEL_HOST}:${MODEL_PORT}"

    if /app/llama-server \
        --model "${MODEL_PATH}" \
        --alias "mobilellm-350m" \
        --host "${MODEL_HOST}" \
        --port "${MODEL_PORT}" \
        --jinja \
        --reasoning off \
        --ctx-size "${CTX}" \
        --threads "${THREADS}" \
        --batch-size 16 \
        --ubatch-size 16 \
        --parallel 1 \
        --no-webui \
        --temp 0.7 \
        --top-k 20 \
        --top-p 0.9 \
        --min-p 0.05 \
        --repeat-penalty 1.1; then
        status=0
    else
        status=$?
    fi

    echo "[MobileLLM-Supervisor] llama.cpp exited with code ${status}; restarting in 2s" >&2
    sleep 2
done
