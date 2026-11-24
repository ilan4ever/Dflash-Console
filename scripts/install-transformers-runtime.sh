#!/usr/bin/env bash
# Linux companion to install-transformers-runtime.ps1:
# install the Transformers / PyTorch runtime under runtimes/transformers/.
# Progress lines use the same "DFLASH_PROGRESS <pct> <message>" format.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TORCH_VARIANT="auto"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --torch-variant) TORCH_VARIANT="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$TORCH_VARIANT" in auto|cuda|cpu) ;; *) echo "--torch-variant must be auto, cuda or cpu" >&2; exit 2 ;; esac

BUNDLE_SRC="$ROOT/runtime-bundles/transformers"
BUNDLE_DEST="$ROOT/runtimes/transformers"
VENV="$BUNDLE_DEST/venv"
VENV_PY="$VENV/bin/python"

if [[ ! -f "$BUNDLE_SRC/server.py" ]]; then
  echo "Missing runtime bundle source: $BUNDLE_SRC" >&2
  exit 1
fi

mkdir -p "$BUNDLE_DEST"
cp -f "$BUNDLE_SRC/server.py" "$BUNDLE_DEST/server.py"
cp -f "$BUNDLE_SRC/requirements.txt" "$BUNDLE_DEST/requirements.txt"

py_ok() {  # prints major.minor when the interpreter is 3.10-3.13
  "$@" -c 'import sys; v=sys.version_info; print(f"{v.major}.{v.minor}") if (v.major,v.minor)>=(3,10) and v.minor<=13 else sys.exit(1)' 2>/dev/null
}

REUSE=0
if [[ -x "$VENV_PY" ]] && py_ok "$VENV_PY" >/dev/null && "$VENV_PY" -m pip --version >/dev/null 2>&1; then
  REUSE=1
fi

if [[ "$REUSE" -eq 1 ]]; then
  echo "DFLASH_PROGRESS 10 Reusing compatible Python environment"
else
  PYBIN=""
  for c in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c" >/dev/null; then PYBIN="$c"; break; fi
  done
  if [[ -z "$PYBIN" ]]; then
    echo "Python 3.10-3.13 is required for the PyTorch/Transformers runtime (sudo apt install python3 python3-venv)." >&2
    exit 2
  fi
  echo "DFLASH_PROGRESS 10 Creating Python environment ($PYBIN)"
  rm -rf "$VENV"
  if ! "$PYBIN" -m venv "$VENV"; then
    echo "Could not create a venv. Install python3-venv: sudo apt install python3-venv" >&2
    exit 2
  fi
fi
[[ -x "$VENV_PY" ]] || { echo "Failed to create venv at $VENV" >&2; exit 1; }

echo "DFLASH_PROGRESS 18 Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip wheel setuptools

USE_CUDA=0
case "$TORCH_VARIANT" in
  cuda) USE_CUDA=1 ;;
  cpu) USE_CUDA=0 ;;
  *) command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1 && USE_CUDA=1 ;;
esac

# On Linux x86_64 the default PyPI torch wheel is the CUDA build (CUDA libraries
# come as pip packages), so no extra index is needed for CUDA.
install_torch() {
  if [[ "$USE_CUDA" -eq 1 ]]; then
    "$VENV_PY" -m pip install "$@" torch
  else
    "$VENV_PY" -m pip install "$@" torch --index-url https://download.pytorch.org/whl/cpu
  fi
}

TORCH_READY=0
"$VENV_PY" -c "import torch" 2>/dev/null && TORCH_READY=1
cuda_ok() { [[ "$("$VENV_PY" -c 'import torch; print(torch.cuda.is_available())' 2>/dev/null)" == "True" ]]; }

if [[ "$TORCH_READY" -eq 0 ]]; then
  if [[ "$USE_CUDA" -eq 1 ]]; then
    echo "DFLASH_PROGRESS 30 Installing PyTorch (CUDA) + Transformers stack..."
  else
    echo "DFLASH_PROGRESS 30 Installing PyTorch (CPU) + Transformers stack..."
  fi
  install_torch
elif [[ "$USE_CUDA" -eq 1 ]] && ! cuda_ok; then
  echo "DFLASH_PROGRESS 30 Restoring the CUDA PyTorch build"
  install_torch --force-reinstall
fi

echo "DFLASH_PROGRESS 70 Installing Transformers libraries"
grep -Ev '^\s*torch(\s|$|\[|[<>=!~])' "$BUNDLE_DEST/requirements.txt" > "$BUNDLE_DEST/requirements-no-torch.txt"
"$VENV_PY" -m pip install --upgrade --no-input --timeout 120 --retries 1 -r "$BUNDLE_DEST/requirements-no-torch.txt"

if [[ "$USE_CUDA" -eq 1 ]] && ! cuda_ok; then
  echo "DFLASH_PROGRESS 82 Restoring the CUDA PyTorch build"
  install_torch --force-reinstall --no-deps
fi

PKG_VERSION="$("$VENV_PY" -c 'import transformers; print(transformers.__version__)' 2>/dev/null | tail -1 || true)"
VARIANT="cpu"; [[ "$USE_CUDA" -eq 1 ]] && VARIANT="cuda"
"$VENV_PY" - "$BUNDLE_DEST" "$VENV_PY" "$VARIANT" "$PKG_VERSION" <<'PY'
import json, sys
from pathlib import Path
dest, py, variant, version = sys.argv[1:5]
manifest = {
    'version': 1,
    'bundle_revision': 1,
    'runtime_id': 'transformers',
    'worker': str(Path(dest) / 'server.py'),
    'python': py,
    'torch_variant': variant,
    'package_version': version.strip(),
    'execution_mode': 'server',
    'generated_by': 'scripts/install-transformers-runtime.sh',
}
(Path(dest) / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
PY

echo "DFLASH_PROGRESS 100 Transformers runtime installed"
echo "Transformers runtime installed at $BUNDLE_DEST"
