#!/bin/sh
set -e

echo "=================================================="
echo " Starting PicoClaw WebUI + PicoLM Stack on Render"
echo " Timestamp: $(date -u)"
echo "=================================================="

# Determine Render / Default Port
PORT="${PORT:-8080}"

# Setup directories
mkdir -p /root/.picoclaw/workspace
mkdir -p /root/.picoclaw/logs
mkdir -p /config

# Setup configuration
if [ -f "/config/config.json" ]; then
    cp /config/config.json /root/.picoclaw/config.json
fi

# Remove stale PID
rm -f /root/.picoclaw/.picoclaw.pid

# Diagnostics
ls -lh /app/picoclaw /app/picoclaw-launcher /app/picolm

# Start PicoLM OpenAI Adapter
python3 /app/scripts/picolm_server.py &
ADAPTER_PID=$!

# Wait for adapter
sleep 5

# Launch Launcher (starts gateway internally)
# -port: WebUI port
# -public: expose
# -no-browser
exec /app/picoclaw-launcher -port "${PORT}" -public -no-browser