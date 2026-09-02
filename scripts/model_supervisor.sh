#!/bin/sh
set -eu

MODEL_HOST="${LOCAL_MODEL_HOST:-127.0.0.1}"
MODEL_PORT="${LOCAL_MODEL_PORT:-8000}"
MODEL_PATH="/app/models/Qwen3.5-0.8B.Q4_K_M.gguf"
THREADS="${LOCAL_MODEL_THREADS:-2}"
CTX="${LOCAL_MODEL_CONTEXT:-4096}"

export LD_LIBRARY_PATH="/opt/llama:${LD_LIBRARY_PATH:-}"

while :; do
    echo "[Qwen-Supervisor] starting llama.cpp on ${MODEL_HOST}:${MODEL_PORT}"

    /app/llama-server \
        --model "${MODEL_PATH}" \
        --alias "qwen3.5-0.8b" \
        --host "${MODEL_HOST}" \
        --port "${MODEL_PORT}" \
        --jinja \
        --reasoning off \
        --ctx-size "${CTX}" \
        --threads "${THREADS}" \
        --batch-size 128 \
        --ubatch-size 64 \
        --parallel 1 \
        --no-webui \
        --temp 0.7 \
        --top-k 20 \
        --top-p 0.9 \
        --min-p 0.05 \
        --repeat-penalty 1.1

    status=$?
    echo "[Qwen-Supervisor] llama.cpp exited with code ${status}; restarting in 2s" >&2
    sleep 2
done
