#!/bin/sh
set -eu

MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
MODEL_PATH="/app/models/SmolVLM-256M-Instruct-Q8_0.gguf"
MMPROJ_PATH="/app/models/mmproj-SmolVLM-256M-Instruct-Q8_0.gguf"
THREADS="${LOCAL_MODEL_THREADS:-1}"
CTX="${LOCAL_MODEL_CONTEXT:-4096}"

while :; do
    echo "[SmolVLM-Supervisor] starting SmolVLM-256M-Instruct on ${MODEL_HOST}:${MODEL_PORT} (ctx=${CTX})"

    if /app/llama-server \
        --model "${MODEL_PATH}" \
        --mmproj "${MMPROJ_PATH}" \
        --alias "smolvlm-256m" \
        --host "${MODEL_HOST}" \
        --port "${MODEL_PORT}" \
        --jinja \
        --ctx-size "${CTX}" \
        --threads "${THREADS}" \
        --batch-size 2 \
        --ubatch-size 2 \
        --parallel 1 \
        --no-webui \
        --no-mmproj-offload \
        --image-max-tokens 256 \
        --mtmd-batch-max-tokens 256 \
        --temp 0.4 \
        --top-k 20 \
        --top-p 0.9 \
        --min-p 0.05 \
        --repeat-penalty 1.1; then
        status=0
    else
        status=$?
    fi

    echo "[SmolVLM-Supervisor] llama.cpp exited with code ${status}; restarting in 2s" >&2
    sleep 2
done
