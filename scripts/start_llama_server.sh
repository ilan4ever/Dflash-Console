#!/usr/bin/env bash
# Linux companion to scripts/start_llama_server.ps1
# Router mode matches what DFlash Console's server_boot expects (Phase 2 wiring).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROFILE="gemma-chat"
PORT=0
HOST_ADDRESS="127.0.0.1"
CONTEXT_SIZE=0
IDLE_UNLOAD_SECONDS=3600
MAIN_GPU=0
SPLIT_MODE="none"
TENSOR_SPLIT=""
GPU_LAYERS=99
CPU_THREADS=9
EVAL_BATCH=2048
PHYSICAL_BATCH=512
FLASH_ATTENTION="on"
KV_OFFLOAD="on"
MODELS_PRESET=""
PARALLEL=0
REASONING_EFFORT="auto"
ROUTER_MODE=0
NO_JINJA=0

usage() {
  cat <<'EOF'
Usage: start_llama_server.sh [options]

Router mode (Console engine listener):
  --router-mode                 Start idle router with --models-preset
  --models-preset PATH          Preset JSON (required with --router-mode)
  --port N                      Listen port
  --host ADDR                   Bind address (default 127.0.0.1)
  --context-size N              Context tokens
  --idle-unload-seconds N       llama-server --sleep-idle-seconds (0 = disabled)
  --main-gpu N                  --main-gpu
  --split-mode none|layer|row   --split-mode
  --tensor-split LIST           --tensor-split
  --ngl N                       -ngl / GPU layers (default 99)
  --threads N                   -t CPU threads
  --batch N                     -b eval batch
  --ubatch N                    -ub physical batch
  --flash-attn on|off           -fa
  --kv-offload on|off           --kv-offload / --no-kv-offload
  --parallel N                  -np parallel slots (router default 4)
  --reasoning-effort LEVEL      auto|none|low|medium|high|max
  --no-jinja                    Pass --no-jinja to llama-server

Profile mode (manual dev; same names as PowerShell script):
  --profile NAME                gemma-chat, gemma-ar, gemma-12-ar, ...
  (other flags apply)

  --help                        Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --router-mode) ROUTER_MODE=1; shift ;;
    --models-preset) MODELS_PRESET="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --host) HOST_ADDRESS="$2"; shift 2 ;;
    --context-size) CONTEXT_SIZE="$2"; shift 2 ;;
    --idle-unload-seconds) IDLE_UNLOAD_SECONDS="$2"; shift 2 ;;
    --main-gpu) MAIN_GPU="$2"; shift 2 ;;
    --split-mode) SPLIT_MODE="$2"; shift 2 ;;
    --tensor-split) TENSOR_SPLIT="$2"; shift 2 ;;
    --ngl) GPU_LAYERS="$2"; shift 2 ;;
    --threads) CPU_THREADS="$2"; shift 2 ;;
    --batch) EVAL_BATCH="$2"; shift 2 ;;
    --ubatch) PHYSICAL_BATCH="$2"; shift 2 ;;
    --flash-attn) FLASH_ATTENTION="$2"; shift 2 ;;
    --kv-offload) KV_OFFLOAD="$2"; shift 2 ;;
    --parallel) PARALLEL="$2"; shift 2 ;;
    --reasoning-effort) REASONING_EFFORT="$2"; shift 2 ;;
    --no-jinja) NO_JINJA=1; shift ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

resolve_config_root() {
  local cfg="$REPO_ROOT/config.json"
  if [[ -f "$cfg" ]]; then
    python3 - "$cfg" <<'PY'
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = str(cfg.get("dflash_root") or "").strip()
models = str(cfg.get("models_root") or "").strip()
if root:
    print(root)
elif models:
    print(str(Path(models).parent))
PY
  fi
}

find_llama_server() {
  local roots=("$REPO_ROOT")
  local cfg_root
  cfg_root="$(resolve_config_root || true)"
  if [[ -n "$cfg_root" && -d "$cfg_root" ]]; then
    roots+=("$cfg_root")
  fi
  local root candidate
  for root in "${roots[@]}"; do
    for candidate in \
      "$root/llama.cpp/build/bin/Release/llama-server" \
      "$root/llama.cpp/build/bin/llama-server" \
      "$root/llama.cpp/build/bin/llama-server.exe"
    do
      if [[ -f "$candidate" ]]; then
        echo "$candidate"
        return 0
      fi
    done
  done
  if [[ -n "${ONEVOICE_ROOT:-}" ]]; then
    for candidate in \
      "$ONEVOICE_ROOT/.tmp/llama-b8418-win-cuda12/llama-server" \
      "$ONEVOICE_ROOT/.tmp/llama-b8418-win-cuda12/llama-server.exe"
    do
      if [[ -f "$candidate" ]]; then
        echo "$candidate"
        return 0
      fi
    done
  fi
  return 1
}

reasoning_args() {
  case "$REASONING_EFFORT" in
    auto) ;;
    none) echo --reasoning off ;;
    low) echo --reasoning on --reasoning-budget 512 ;;
    medium) echo --reasoning on --reasoning-budget 2048 ;;
    high) echo --reasoning on --reasoning-budget 8192 ;;
    max) echo --reasoning on --reasoning-budget -1 ;;
    *) echo "Invalid --reasoning-effort: $REASONING_EFFORT" >&2; exit 1 ;;
  esac
}

MAIN_BIN="$(find_llama_server)" || {
  echo "llama-server not found under $REPO_ROOT (build llama.cpp or set dflash_root)" >&2
  exit 1
}

if [[ "$ROUTER_MODE" -eq 1 ]]; then
  if [[ -z "$MODELS_PRESET" ]]; then
    echo "--router-mode requires --models-preset" >&2
    exit 1
  fi
  if [[ ! -f "$MODELS_PRESET" ]]; then
    echo "Models preset not found: $MODELS_PRESET" >&2
    exit 1
  fi
  [[ "$PORT" -gt 0 ]] || PORT=8090
  [[ "$CONTEXT_SIZE" -gt 0 ]] || CONTEXT_SIZE=65536
  [[ "$GPU_LAYERS" -gt 0 ]] || GPU_LAYERS=99
  [[ "$CPU_THREADS" -gt 0 ]] || CPU_THREADS=9
  [[ "$EVAL_BATCH" -gt 0 ]] || EVAL_BATCH=2048
  [[ "$PHYSICAL_BATCH" -gt 0 ]] || PHYSICAL_BATCH=512
  [[ "$PARALLEL" -gt 0 ]] || PARALLEL=4

  args=(
    --host "$HOST_ADDRESS"
    --port "$PORT"
    -c "$CONTEXT_SIZE"
    -np "$PARALLEL"
    -ngl "$GPU_LAYERS"
    -t "$CPU_THREADS"
    -fa "$FLASH_ATTENTION"
    -b "$EVAL_BATCH"
    -ub "$PHYSICAL_BATCH"
    --main-gpu "$MAIN_GPU"
    --split-mode "$SPLIT_MODE"
    --models-preset "$MODELS_PRESET"
    --no-models-autoload
    --fit off
  )
  if [[ "$NO_JINJA" -eq 1 ]]; then
    args+=(--no-jinja)
  else
    args+=(--jinja)
  fi
  if [[ "$KV_OFFLOAD" == "off" ]]; then
    args+=(--no-kv-offload)
  else
    args+=(--kv-offload)
  fi
  if [[ "$IDLE_UNLOAD_SECONDS" -gt 0 ]]; then
    args+=(--sleep-idle-seconds "$IDLE_UNLOAD_SECONDS")
  fi
  if [[ -n "$TENSOR_SPLIT" ]]; then
    args+=(--tensor-split "$TENSOR_SPLIT")
  fi
  if [[ "$REASONING_EFFORT" != "auto" ]]; then
    # shellcheck disable=SC2206
    extra=($(reasoning_args))
    args+=("${extra[@]}")
  fi

  echo "=== llama-server router ==="
  echo "Binary: $MAIN_BIN"
  echo "Port:   $PORT   Preset: $MODELS_PRESET"
  echo "API:    http://${HOST_ADDRESS}:${PORT}/v1"
  exec "$MAIN_BIN" "${args[@]}"
fi

echo "Profile mode ($PROFILE) is for manual use on Linux."
echo "DFlash Console auto-boot will use --router-mode after Phase 2."
echo "Binary: $MAIN_BIN"
exit 1
