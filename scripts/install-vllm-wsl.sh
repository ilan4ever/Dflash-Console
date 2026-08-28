#!/usr/bin/env bash
set -euo pipefail
PYBIN=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    PYBIN="$c"
    break
  fi
done
if [ -z "$PYBIN" ]; then
  echo "Python 3 is missing in WSL. Open Ubuntu and run: sudo apt update && sudo apt install -y python3 python3-venv python3-pip" >&2
  exit 2
fi
echo "DFLASH_PROGRESS 45 Using $PYBIN in WSL"
VENV="$HOME/.dflash-console/vllm-venv"
mkdir -p "$HOME/.dflash-console"
if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "DFLASH_PROGRESS 48 Removing incomplete WSL environment"
  rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  echo "DFLASH_PROGRESS 50 Creating WSL Python environment"
  if ! "$PYBIN" -m venv "$VENV"; then
    echo "DFLASH_PROGRESS 52 python3-venv missing — bootstrapping pip without it"
    rm -rf "$VENV"
    "$PYBIN" -m venv --without-pip "$VENV"
    GETPIP="$(mktemp)"
    "$PYBIN" -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', r'''$GETPIP''')"
    "$VENV/bin/python" "$GETPIP"
    rm -f "$GETPIP"
  fi
fi
if [ ! -x "$VENV/bin/python" ]; then
  echo "Failed to create a WSL Python environment for vLLM." >&2
  exit 2
fi
echo "DFLASH_PROGRESS 55 Upgrading pip in WSL"
"$VENV/bin/python" -m pip install --upgrade pip wheel setuptools
echo "DFLASH_PROGRESS 60 Downloading vLLM in WSL"
"$VENV/bin/python" -m pip install vllm
echo "DFLASH_PROGRESS 90 Checking vLLM import"
"$VENV/bin/python" -c "import vllm"
printf '%s\n' "$VENV/bin/python"
