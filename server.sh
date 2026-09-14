#!/usr/bin/env bash
# Linux companion to server.ps1 — start the Console API (no Electron).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PORT=0
FOREGROUND=0
RESTART=0

usage() {
  cat <<'EOF'
Usage: ./server.sh [options]

  --port N          UI port (default from config.json or 8900)
  --foreground      Run uvicorn in this terminal (do not background)
  --restart         Stop existing listener on the port first
  --help            Show this help

Environment:
  DFLASH_CONSOLE_ROOT / DFLASH_ROOT — data root (default: this directory)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --foreground) FOREGROUND=1; shift ;;
    --restart) RESTART=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ "$PORT" -le 0 ]]; then
  if [[ -f "$ROOT/config.json" ]]; then
    PORT="$(python3 -c "import json; print(int(json.load(open('$ROOT/config.json')).get('ui_port') or 8900))")"
  else
    PORT=8900
  fi
fi

export DFLASH_CONSOLE_ROOT="${DFLASH_CONSOLE_ROOT:-$ROOT}"
export DFLASH_ROOT="${DFLASH_ROOT:-$DFLASH_CONSOLE_ROOT}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p "$ROOT/logs"
log_line() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$ROOT/logs/startup.log"; }

log_line "=== DFlash Console startup (Linux) ==="
log_line "Root: $ROOT"
log_line "DFlash root: $DFLASH_ROOT"
log_line "Console UI port: $PORT"

health_ok() {
  curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1
}

if [[ "$RESTART" -eq 1 ]] && health_ok; then
  log_line "Requesting graceful shutdown on port $PORT"
  curl -sf -X POST "http://127.0.0.1:${PORT}/api/shutdown" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    health_ok || break
    sleep 0.3
  done
fi

if command -v dflash >/dev/null 2>&1; then
  SERVE_CMD=(dflash serve --port "$PORT")
else
  SERVE_CMD=(python3 -m uvicorn api.app:app --host 127.0.0.1 --port "$PORT")
fi

log_line "Starting Console API: ${SERVE_CMD[*]}"
echo ""
echo "DFlash Console → http://127.0.0.1:${PORT}/"
echo ""

if [[ "$FOREGROUND" -eq 1 ]]; then
  exec "${SERVE_CMD[@]}"
fi

nohup "${SERVE_CMD[@]}" >>"$ROOT/logs/console-server.log" 2>&1 &
echo "PID $! — log: $ROOT/logs/console-server.log"
