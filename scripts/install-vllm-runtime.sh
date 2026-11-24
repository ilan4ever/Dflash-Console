#!/usr/bin/env bash
# Linux companion to install-vllm-runtime.ps1: native vLLM under runtimes/vllm/venv.
# (vLLM is Linux-first, so there is no WSL step here.)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --backend) shift 2 ;;  # accepted for parity with the .ps1; Linux is always native
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

BUNDLE_DEST="$ROOT/runtimes/vllm"
VENV="$BUNDLE_DEST/venv"
VENV_PY="$VENV/bin/python"
mkdir -p "$BUNDLE_DEST" "$ROOT/logs/runtimes"

echo "DFLASH_PROGRESS 8 Finding Python"
PYBIN=""
for c in python3.12 python3.11 python3.10 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1 \
    && "$c" -c 'import sys; v=sys.version_info; sys.exit(0 if (3,10)<=(v.major,v.minor)<=(3,13) else 1)' 2>/dev/null; then
    PYBIN="$c"; break
  fi
done
if [[ -z "$PYBIN" ]]; then
  echo "Python 3.10-3.13 is required for vLLM (sudo apt install python3 python3-venv)." >&2
  exit 2
fi

if [[ -x "$VENV_PY" ]] && ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
  echo "DFLASH_PROGRESS 10 Removing incomplete environment"
  rm -rf "$VENV"
fi
if [[ ! -x "$VENV_PY" ]]; then
  echo "DFLASH_PROGRESS 12 Creating Python environment ($PYBIN)"
  "$PYBIN" -m venv "$VENV" || { echo "Could not create a venv. Install python3-venv: sudo apt install python3-venv" >&2; exit 2; }
fi

echo "DFLASH_PROGRESS 18 Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip wheel setuptools

echo "DFLASH_PROGRESS 30 Downloading vLLM (several GB, this takes a while)"
"$VENV_PY" -m pip install --upgrade vllm

echo "DFLASH_PROGRESS 90 Checking vLLM import"
"$VENV_PY" -c "import vllm"
PKG_VERSION="$("$VENV_PY" -c 'import vllm; print(vllm.__version__)' 2>/dev/null | tail -1 || true)"

"$VENV_PY" - "$BUNDLE_DEST/manifest.json" "$VENV_PY" "$PKG_VERSION" <<'PY'
import json, sys
path, py, version = sys.argv[1:4]
manifest = {
    'version': 1,
    'bundle_revision': 1,
    'runtime_id': 'vllm',
    'execution_mode': 'server',
    'backend': 'native',
    'python': py,
    'wsl_distro': '',
    'wsl_python': '',
    'package_version': version.strip(),
    'generated_by': 'scripts/install-vllm-runtime.sh',
}
open(path, 'w', encoding='utf-8').write(json.dumps(manifest, indent=2) + '\n')
PY

echo "DFLASH_PROGRESS 100 vLLM runtime installed"
echo "vLLM runtime installed at $BUNDLE_DEST"
