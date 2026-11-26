#!/usr/bin/env bash
# Open DFlash Console on Linux: start the server if it is not running, then
# show the UI in its own app window. The menu/desktop launcher runs this.
#
# An app window (Chrome/Chromium --app) gets its own taskbar button with the
# DFlash icon. A normal browser tab would show the browser's icon instead.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PORT=8900
if [[ -f "$ROOT/config.json" ]]; then
  PORT="$(python3 -c "import json,sys; print(int(json.load(open(sys.argv[1])).get('ui_port') or 8900))" "$ROOT/config.json" 2>/dev/null || echo 8900)"
fi
URL="http://127.0.0.1:${PORT}/"

health_ok() { curl -sf "${URL}api/health" >/dev/null 2>&1; }

if ! health_ok; then
  # A user service (set up on servers that run DFlash at boot) owns the
  # process; otherwise start it in the background with server.sh.
  if systemctl --user cat dflash-console.service >/dev/null 2>&1; then
    systemctl --user start dflash-console.service
  else
    "$ROOT/server.sh" --port "$PORT" >/dev/null 2>&1
  fi
  for _ in $(seq 1 60); do
    health_ok && break
    sleep 0.5
  done
fi

for browser in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge brave-browser; do
  if command -v "$browser" >/dev/null 2>&1; then
    exec "$browser" --app="$URL"
  fi
done
exec xdg-open "$URL"
