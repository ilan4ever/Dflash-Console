# DFlash Console

**Local control panel for DFlash speculative-decoding stacks** — manage llama-server engines, checkpoint libraries, Hugging Face downloads, and GPU settings from a single web UI.

> **Status:** Private development repository. Open-source release planned; not published yet.

**UI:** [http://127.0.0.1:8900/](http://127.0.0.1:8900/)

---

## What it does

DFlash Console is a standalone FastAPI + vanilla JavaScript app that sits beside your [DFlash](https://github.com/ilan4ever/Dflash) install. It gives you an LM Studio–style workbench focused on **your** engine profiles—not a generic chat client.

| Area | Highlights |
|------|------------|
| **Server** | Start/stop llama-server, load & eject checkpoints, live boot progress, developer logs |
| **Models** | Scan local GGUF, Piper, Whisper, OCR, and embedding folders across multiple library roots |
| **Search** | Browse and download Hugging Face models into configured library locations |
| **Settings** | GPU strategy, checkpoint storage, engine network/API, MCP client preview |
| **Discovery** | **Scan PC** finds model folders; **Add folder** opens a drive-aware browser (C:, D:, …) |

Router mode uses `--models-preset` with load/unload over HTTP so engines stay listening while models swap.

---

## Requirements

- **Windows** (primary target; PowerShell startup scripts)
- **Python 3.10+**
- **PowerShell 7+** (`pwsh`)
- Built **llama-server** under your DFlash tree (see `DFLASH_ROOT`)
- NVIDIA GPU optional but recommended for multi-GPU load settings

---

## Quick start

```powershell
git clone https://github.com/ilan4ever/Dflash-Console.git
cd Dflash-Console

# First-time setup
copy config.example.json config.json
# Edit config.json — set dflash_root, server ports, and model paths

pip install -r requirements.txt
.\run.ps1
```

Open **http://127.0.0.1:8900/** in your browser.

Foreground mode (attach logs to the terminal):

```powershell
.\run.ps1 -Foreground
```

Restart API after backend edits:

```powershell
.\scripts\restart-console-server.ps1
```

The UI auto-refreshes when the API restarts (`/api/health` boot id).

---

## Configuration

| Variable / file | Purpose |
|-----------------|--------|
| `config.json` | Local settings (not committed — copy from `config.example.json`) |
| `DFLASH_ROOT` | Path to DFlash repo with llama-server binaries and launch scripts |
| `config.json` → `servers[]` | Engine profiles: port, GPU layers, context, idle unload |
| `config.json` → `model_libraries[]` | Folders scanned for local models and HF download targets |

Default UI port is **8900**. Engine ports (e.g. 8090, 8092) are configured per server block.

---

## Project layout

```
Dflash-Console/
├── api/app.py              # FastAPI routes
├── core/                   # Config, runtime, model discovery, HF, GPU
├── static/                 # UI (HTML, CSS, JS)
├── scripts/                # Server start / restart helpers
├── tests/                  # pytest suite
├── docs/ui/                # UI panel notes
├── run.ps1                 # Full startup (deps + background API)
├── config.example.json     # Template configuration
└── DFLASH-CONSOLE-PLAN.md   # Detailed build plan & handoff notes
```

---

## API (selected)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Liveness + `boot_id` (UI reload watch) |
| `GET` | `/api/servers` | All engine statuses |
| `POST` | `/api/servers/{id}/start` | Boot router / load checkpoint |
| `POST` | `/api/servers/{id}/unload` | Eject model (router stays up) |
| `GET` | `/api/models` | Local catalog from enabled libraries |
| `GET` | `/api/model-libraries/scan` | PC scan for model folders |
| `GET` | `/api/fs/browse` | Folder picker for library paths |
| `GET` | `/api/hf/search` | Hugging Face model search |
| `POST` | `/api/hf/download` | Download GGUF into a library |

Full route list: `api/app.py`.

---

## Development

```powershell
pip install -r requirements.txt
pytest
.\run.ps1 -Foreground
```

Cursor agents: see `.cursor/rules/dev-server-restart.mdc` — restart the API after Python backend changes.

---

## Related projects

- **DFlash** — speculative decoding stack and llama-server profiles this console manages
- Binaries, draft models, and `start_llama_server.ps1` live under `DFLASH_ROOT`, not in this repo

---

## License

Not yet published for open source. License will be added before public release.

---

## Roadmap

See [DFLASH-CONSOLE-PLAN.md](./DFLASH-CONSOLE-PLAN.md) for architecture, handoff notes, and remaining work (chat tab, live VRAM sysbar, expanded tests).
