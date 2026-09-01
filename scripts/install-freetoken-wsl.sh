#!/usr/bin/env bash
set -euo pipefail

PYBIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYBIN="$candidate"
    break
  fi
done

if [ -z "$PYBIN" ]; then
  echo "Python 3.10+ is required inside WSL. Install python3, python3-venv, and python3-pip." >&2
  exit 2
fi

VENV="$HOME/.dflash-console/freetoken-venv"
mkdir -p "$HOME/.dflash-console"

if [ ! -x "$VENV/bin/python" ]; then
  echo "DFLASH_PROGRESS 20 Creating FreeToken WSL environment"
  "$PYBIN" -m venv "$VENV"
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "FreeToken WSL Python environment could not be created." >&2
  exit 2
fi

echo "DFLASH_PROGRESS 35 Upgrading WSL packaging tools"
"$VENV/bin/python" -m pip install --upgrade pip wheel
echo "DFLASH_PROGRESS 55 Installing FreeToken CUDA runtime"
"$VENV/bin/python" -m pip install --upgrade "freetoken[accel]"
echo "DFLASH_PROGRESS 90 Verifying FreeToken import"
"$VENV/bin/python" -c "import freetoken; print(freetoken.__version__)"
printf 'FREETOKEN_WSL_PYTHON=%s\n' "$VENV/bin/python"
printf 'FREETOKEN_WSL_FT=%s\n' "$VENV/bin/ft"
