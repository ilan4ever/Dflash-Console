#!/usr/bin/env bash
# Linux companion to install-freetoken-runtime.ps1: FreeToken runs natively here (no WSL).
# Reuses install-freetoken-wsl.sh, which is plain Linux bash, then writes a "native" manifest.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

BUNDLE="$ROOT/runtimes/freetoken"
mkdir -p "$BUNDLE"
echo "DFLASH_PROGRESS 8 Preparing FreeToken environment"

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
bash "$ROOT/scripts/install-freetoken-wsl.sh" | tee "$OUT"

PY="$(grep '^FREETOKEN_WSL_PYTHON=' "$OUT" | tail -1 | cut -d= -f2-)"
FT="$(grep '^FREETOKEN_WSL_FT=' "$OUT" | tail -1 | cut -d= -f2-)"
[[ -n "$PY" ]] || { echo "FreeToken installed but its Python path was not reported." >&2; exit 1; }
[[ -n "$FT" ]] || FT="$(dirname "$PY")/ft"

echo "DFLASH_PROGRESS 94 Checking the ft command"
if ! "$FT" --version; then
  echo "FreeToken was installed, but 'ft --version' failed. Check the NVIDIA driver and CUDA." >&2
  exit 1
fi
FT_VERSION="$("$FT" --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+[0-9.]*' | head -1 || true)"

"$PY" - "$BUNDLE/manifest.json" "$PY" "$FT" "$FT_VERSION" <<'PY'
import json, sys
path, py, ft, version = sys.argv[1:5]
manifest = {
    'version': 1,
    'bundle_revision': 1,
    'runtime_id': 'freetoken',
    'execution_mode': 'server',
    'backend': 'native',
    'python': py,
    'ft': ft,
    'wsl_distro': '',
    'wsl_python': '',
    'wsl_ft': '',
    'package_version': version,
    'generated_by': 'scripts/install-freetoken-runtime.sh',
}
open(path, 'w', encoding='utf-8').write(json.dumps(manifest, indent=2) + '\n')
PY

echo "DFLASH_PROGRESS 100 FreeToken runtime installed"
echo "FreeToken runtime installed (native) — ft: $FT"
