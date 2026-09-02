#!/bin/sh
set -eu
while :; do
    echo '[Router-Supervisor] starting MobileLLM-aware model router'
    if python3 /app/scripts/router_server.py; then
        status=0
    else
        status=$?
    fi
    echo "[Router-Supervisor] router exited with code ${status}; restarting in 2s" >&2
    sleep 2
done
