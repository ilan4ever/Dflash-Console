# DFlash Console — Linux CLI (experimental)

**Status:** Experimental — not supported or tested as a product yet.

Windows remains the only supported platform for the full app (installer + UI).
This document describes the **CLI-only** path on Linux: run the Console API and
use `dflash` from the terminal. The web UI still works in a browser at
`http://127.0.0.1:8900/` if you start the server, but there is no Linux
desktop shell.

---

## What works today (Phase 1)

| Piece | Linux today |
|-------|-------------|
| `dflash serve` | Yes — starts FastAPI/uvicorn (Python) |
| `dflash status`, `list`, `chat`, … | Yes — HTTP client against the API |
| Browser UI at `:8900` | Yes — same static UI as Windows |
| OpenAI gateway `:8001` | Yes — when enabled in config |
| `nvidia-smi` GPU discovery | Yes — NVIDIA drivers required |
| External GPU process cards | No — Windows-specific scan |
| Desktop Electron app | No — Windows only |
| **Auto engine boot** (`llama-server`) | **Not yet** — see [Engine launcher](#engine-launcher) below |

---

## Roadmap

### Phase 1 — Runs and serves (current)

1. Linux shell scripts beside the existing PowerShell scripts (no Windows changes).
2. Document install, WSL2 testing, and known gaps.
3. `dflash serve` + CLI + browser UI smoke test on Ubuntu/WSL2.

### Phase 2 — Loads a model

1. Small platform branch in `core/server_boot.py` to call `scripts/start_llama_server.sh`
   instead of `.ps1` on Linux (requires maintainer approval — not done yet).
2. Linux CUDA build of `llama-server` under `dflash_root` (see below).
3. Load / chat / unload through `dflash load` and the API.

### Phase 3 — CI and polish

1. `ubuntu-latest` job in GitHub Actions (import + serve smoke, no GPU).
2. Linux install section in README; optional `.deb` / tarball later.

---

## Engine launcher

The Console starts `llama-server` through `scripts/start_llama_server.ps1` on
Windows (`core/server_boot.py`).

**Phase 1 adds** `scripts/start_llama_server.sh` with the same **router mode**
interface the Console expects, but the Python code still points at the `.ps1`
file only. Until that one platform check is added (with your approval), on
Linux you can:

- Run **`dflash serve`** and use CLI/API for everything that does not need a
  local GGUF engine, or
- Start **`llama-server` manually** using the shell script (see below).

---

## Quick start (git checkout)

### Native Linux or WSL2 Ubuntu

```bash
cd /path/to/Dflash-Console
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp -n config.example.json config.json   # if you have no config yet
export DFLASH_CONSOLE_ROOT="$PWD"
export DFLASH_ROOT="$PWD"               # optional; defaults to data root

dflash serve
```

In another terminal:

```bash
source .venv/bin/activate
dflash status
dflash list
```

Open **http://127.0.0.1:8900/** in a browser for the same UI as Windows.

Or use the helper scripts:

```bash
chmod +x run.sh server.sh scripts/*.sh
./scripts/setup-linux-dev.sh          # first-time venv + deps
./run.sh                              # same as dflash serve
./run.sh --port 8900 --foreground     # attach uvicorn to this terminal
```

### WSL2 on Windows (recommended for developers)

You already use WSL2 for FreeToken. Use the same Ubuntu distro:

1. Install [NVIDIA driver on Windows](https://developer.nvidia.com/cuda/wsl)
   (WSL2 GPU support).
2. Inside WSL: `nvidia-smi` should show your GPU.
3. Clone or access the repo via `/mnt/c/dev/Dflash-Console` (or a Linux home copy).
4. Follow **Quick start** above.

**Tip:** Prefer a Linux path under `$HOME` for venv and models if Windows
filesystem performance is slow; keep one `DFLASH_CONSOLE_ROOT` and sync
`config.json` as needed.

---

## llama-server on Linux (manual / Phase 2)

Build or place a Linux `llama-server` binary. The Console looks for:

```text
$dflash_root/llama.cpp/build/bin/Release/llama-server
$dflash_root/llama.cpp/build/bin/llama-server
```

Or the checkout that owns `models_root` in `config.json`.

Example router start (matches what the Console will spawn after Phase 2):

```bash
./scripts/start_llama_server.sh \
  --router-mode \
  --models-preset /path/to/preset.json \
  --port 8090 \
  --host 127.0.0.1 \
  --context-size 65536 \
  --idle-unload-seconds 3600 \
  --main-gpu 0 \
  --split-mode none \
  --parallel 4
```

Run `scripts/start_llama_server.sh --help` for all flags.

---

## Testing options on a Windows PC

| Method | GPU | Best for |
|--------|-----|----------|
| **WSL2 Ubuntu** | Yes (NVIDIA WSL driver) | Daily Linux dev on your machine |
| **GitHub Actions `ubuntu-latest`** | No | CI smoke tests |
| **Docker** | Harder (need NVIDIA Container Toolkit) | Reproducible env |
| **Remote Linux VPS** | Optional | CPU-only API/CLI tests |

---

## Known limitations (Linux)

- No external GPU app detection (Ollama/LM Studio cards on Engines tab).
- CPU/RAM sysbar stats may be incomplete (Windows uses PowerShell counters).
- `dflash install` is aimed at Windows PATH/profile setup.
- FreeToken on native Linux may differ from the Windows+WSL2 path — report results.
- PyPI wheel may not include `scripts/`; use a **git checkout** or full sdist for shell scripts.

---

## Feedback

We want reports from real Linux installs. Please open a
[Discussion](https://github.com/ilan4ever/Dflash-Console/discussions) or
[Issue](https://github.com/ilan4ever/Dflash-Console/issues/new?template=bug_report.yml)
with:

- Distro and version (e.g. Ubuntu 24.04, WSL2)
- Install path (`pip`, git checkout)
- `dflash serve` / `dflash status` output
- Whether `nvidia-smi` works
- What you tried to load (if any)

This helps prioritize Phase 2 (engine boot) and CI.
