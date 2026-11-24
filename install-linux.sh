#!/usr/bin/env bash
# One-step DFlash Console install for Ubuntu / Linux Mint (24.04 base).
# Installs system packages, the Python venv and dependencies, and a menu/desktop launcher.
# Safe to run again (updates in place). Usage:  bash install-linux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
echo "=== DFlash Console — Linux install ==="
echo "Folder: $ROOT"

# 1. System packages (needs sudo once)
PKGS=(python3 python3-venv python3-dev python3-pip build-essential git curl)
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
if (( ${#MISSING[@]} )); then
  echo "Installing system packages: ${MISSING[*]}"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING[@]}"
else
  echo "System packages: OK"
fi

# 2. Python venv + DFlash Console and its dependencies
chmod +x run.sh server.sh scripts/*.sh
./scripts/setup-linux-dev.sh

# 3. Launcher in the app menu and on the desktop
LAUNCHER="$HOME/.local/share/applications/dflash-console.desktop"
mkdir -p "$(dirname "$LAUNCHER")"
ICON="$ROOT/static/favicon.ico"; [[ -f "$ICON" ]] || ICON="utilities-terminal"
cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=DFlash Console
Comment=Start DFlash Console and open it in the browser
Exec=bash -c '"$ROOT/server.sh" --restart >/dev/null 2>&1; for i in \$(seq 1 40); do curl -sf http://127.0.0.1:8900/api/health >/dev/null && break; sleep 0.5; done; xdg-open http://127.0.0.1:8900/'
Icon=$ICON
Terminal=false
Categories=Development;
EOF
chmod +x "$LAUNCHER"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [[ -d "$DESKTOP_DIR" ]]; then
  cp "$LAUNCHER" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/dflash-console.desktop"
  gio set "$DESKTOP_DIR/dflash-console.desktop" metadata::trusted true 2>/dev/null || true
fi

echo ""
echo "=== Installed ==="
echo "Start: 'DFlash Console' in the menu / on the desktop, or:  $ROOT/run.sh"
echo "UI:    http://127.0.0.1:8900/"
