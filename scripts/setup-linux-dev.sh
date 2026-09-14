#!/usr/bin/env bash
# First-time Linux / WSL2 dev setup for DFlash Console CLI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== DFlash Console — Linux dev setup ==="
echo "Root: $ROOT"
echo ""

if ! command -v python3 >/dev/null; then
  echo "python3 is required. Install Python 3.10+ and retry." >&2
  exit 1
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Python: $PY_VER"

if [[ ! -d .venv ]]; then
  echo "Creating .venv ..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing package (editable) ..."
pip install -U pip
pip install -e ".[dev]"

if [[ ! -f config.json ]]; then
  if [[ -f config.example.json ]]; then
    cp config.example.json config.json
    echo "Created config.json from config.example.json"
  fi
fi

export DFLASH_CONSOLE_ROOT="$ROOT"
export DFLASH_ROOT="$ROOT"

echo ""
echo "Setup complete."
echo ""
echo "  source .venv/bin/activate"
echo "  export DFLASH_CONSOLE_ROOT=\"$ROOT\""
echo "  dflash serve"
echo ""
echo "Or:  ./run.sh"
echo ""

if command -v nvidia-smi >/dev/null; then
  echo "GPU:"
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || true
else
  echo "nvidia-smi not found — CPU-only mode unless you install NVIDIA drivers."
fi

echo ""
echo "See docs/LINUX-CLI.md for the full Linux plan and limitations."
