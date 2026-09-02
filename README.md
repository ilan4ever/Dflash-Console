# DFlash Console

**Local control panel for DFlash speculative-decoding stacks and a unified local model runtime** — manage llama-server engines, Piper TTS, Whisper STT, embeddings, model libraries, Hugging Face downloads, and GPU settings from a single web UI.

> **Status:** Public preview. This project is intended for local, single-user Windows deployments.

**Developer:** ILAN AVIV · **UI:** [http://127.0.0.1:8900/](http://127.0.0.1:8900/) · **Version:** v0.3.117

## Download (Windows)

| Package | Link |
|---------|------|
| **Setup installer** (recommended) | [Latest GitHub Release](https://github.com/ilan4ever/Dflash-Console/releases/latest) — `DFlash-Console-Setup-*-x64.exe` |
| **Portable** | Same Releases page — `DFlash-Console-Portable-*-x64.exe` when published |
| **CLI only** | `pip install dflash-console` then `dflash serve` |

Installed desktop apps check for updates through a separate signed feed (not GitHub).
New installs: use the GitHub Release installer above.

---

## What it does

DFlash Console is a standalone FastAPI + vanilla JavaScript app that sits beside your [DFlash](https://github.com/ilan4ever/Dflash) installation. It gives you an LM Studio–style workbench focused on **your** engine profiles—not a generic chat client.

| Area | Highlights |
|------|------------|
| **Engines** | Start/stop llama-server routers, load & eject models in parallel, live boot progress, token stats on cards, developer logs with clear button |
| **Runtimes** | Unified multi-modal layer: FreeToken WSL, Piper TTS, whisper.cpp STT, embeddings — Console-proxied OpenAI routes, adapter registry, shared ports, process identity |
| **Models** | Full PC library: DFlash, GGUF, Ollama, LM Studio, Piper, Whisper, OCR, and embeddings |
| **Model catalog** | Browse and download Hugging Face models into configured library locations |
| **Settings** | GPU strategy, model storage, engine network/API, MCP client preview, **Locations** panel with config/preset import-export |
| **Documentation** | In-app API reference, user guide, terminal CLI, and release notes |
| **Terminal CLI** | `dflash` command: list, load, chat, embed, delete, nodes, settings, search, pull. Install with `pip install dflash-console` |
| **About** | Developer attribution, version, license, runtime boundary, and public project links |
| **Discovery** | **Scan PC** finds model folders; **Add folder** opens a drive-aware browser (C:, D:, …) |

Router mode uses `--models-preset` with load/unload over HTTP so engines stay listening while models swap.

---

## Multi-modal runtime

DFlash Console is now a **unified local model runtime**, not just a GGUF control
panel. A small **adapter registry** (`core/runtimes/`) lets the Console discover,
load, monitor, and proxy OpenAI-shaped APIs for many model families, while
`llama-server` / DFlash stacks stay the first-class GGUF path.

| Modality | Runtime | Engine | Mode |
|----------|---------|--------|------|
| LLM chat / instruct | `llama-server` | GGUF (existing) | server |
| LLM chat (SafeTensors) | **vLLM**, **Transformers**, or **FreeToken** | Hugging Face folders; FreeToken uses WSL2/Linux | server |
| Embeddings | `llama-server` embedding profile | GGUF (existing) | server |
| Text-to-speech | **Piper** | ONNX voices (`runtimes/piper/`) | CLI |
| Speech-to-text | **whisper.cpp `whisper-server`** | GGUF-whisper (`runtimes/stt/`) | server |
| Vision / OCR | `llama-server` + mmproj | GGUF + projector (existing) | server |

### Runtime bundles (thin installer / fat data root)

Engines and voices live **outside** the Electron installer under the Console
data root:

```
runtimes/
├── piper/                 # piper.exe + espeak-ng-data + voices/*.onnx(+json)
│   └── manifest.json
├── stt/                   # whisper-server.exe + ggml-cuda.dll + ...
│   └── manifest.json
├── freetoken/             # WSL manifest + managed process state (Linux venv stays in WSL)
│   └── manifest.json
└── process-tokens.json    # shared managed-process identity (read by server.ps1)
```

Each adapter writes a `manifest.json` at boot, exposed read-only at
`GET /api/runtimes/manifests`. Piper and Whisper ship as native bundles.
**vLLM**, **Transformers**, and **FreeToken** download on demand from Settings or
the first-run wizard so the Windows installer stays small. FreeToken's Python
and CUDA environment remains inside WSL.

### Playground modes

The Playground has four modes:

| Mode | What it does |
|------|--------------|
| **Chat** | Chat completions against llama-server / DFlash, vLLM, Transformers, or FreeToken when that engine is loaded |
| **Speak** | Text → Piper WAV (voice picker, speed, download) |
| **Transcribe** | Pick a Whisper model → upload audio → transcript |
| **Embed** | One item per line → vectors; **Export .jsonl** |

### Console-proxied OpenAI routes

Children stay on internal loopback ports; clients only talk to the Console:

| Route | Upstream |
|-------|----------|
| `/api/servers/{id}/v1/chat/completions` | llama-server (existing) |
| `/api/servers/freetoken/v1/chat/completions` | FreeToken through WSL2 (OpenAI-compatible) |
| `/api/runtimes/piper/v1/audio/speech` | Piper (CLI) |
| `/api/runtimes/stt/v1/audio/transcriptions` | whisper-server (multipart → `/inference`) |
| `/api/servers/{id}/v1/embeddings` | llama-server embedding profile |
| `/api/servers/{id}/embed/batch` | batch embed + `.jsonl` export |

### Console OpenAI gateway (port 8001)

Point any OpenAI-compatible app at one stable base URL —
`http://127.0.0.1:8001/v1` — and the gateway proxies to the loaded engine
(chat, embeddings, TTS, STT). Model names are tolerant (engine id, checkpoint
id, or any alias like `gpt-4o`); chat JIT-loads and streams SSE. Configure in
**Settings → Engine profiles → Console OpenAI gateway**
(`gateway_port`, `gateway_server_id`); see [USER-GUIDE §8](docs/USER-GUIDE.md).

### Process identity & cleanup

Every adapter contributes **path-specific** process-identity tokens
(`runtimes\piper\piper.exe`, `runtimes\stt\whisper-server`) to a shared
registry. `managed_process_identity()`, `server.ps1` stop/shutdown cleanup, and
restart adoption recognise Console-managed children **without** ever adopting
foreign processes (e.g. another app's bundled Piper). Stop/unload kills children
and frees VRAM with no orphans.

### Config

Non-llama runtimes are configured in `config.json` under `runtimes[]`; the
`servers[]` list stays exclusively for llama-server / DFlash / GGUF:

```json
"runtimes": [
  { "id": "tts-main", "runtime_id": "piper", "port": 0, "device_policy": "cpu" },
  { "id": "stt-main", "runtime_id": "stt",  "port": 8910, "device_policy": "gpu" }
]
```

`GET /api/runtimes` merges `servers[]` (synthesised `runtime_id: llama-server`)
with `runtimes[]`. Ports are unique across ui/servers/runtimes (shared
registry); CLI runtimes use `port: 0`.

### FreeToken on Windows

FreeToken is optional and is not a native Windows engine. Install it from
**Settings → Downloads & engines**. The installer detects an Ubuntu/Debian WSL2
distribution, creates a Linux virtual environment there, installs the CUDA
package, and records its distro and `ft` path in `runtimes/freetoken/manifest.json`.
WSL2 GPU passthrough, a compatible NVIDIA Windows driver, and CUDA 13 support
are required. FreeToken supports only the model families and formats listed in
its upstream documentation; multimodal checkpoints are served text-only.
Windows model folders are translated to `/mnt/<drive>/...`. For large models,
storing the files inside WSL may perform better.

---

## Recent improvements

### v0.3.105 — public preview documentation

| Feature | Description |
|---------|-------------|
| **Docs** | README, user guide, CLI, About page, and in-app Documentation aligned for launch |
| **Install** | GitHub Releases + PyPI + single-server rule documented everywhere |

### v0.3.104 — pip on PyPI and single-server takeover

| Feature | Description |
|---------|-------------|
| **PyPI** | `pip install dflash-console` is live on [PyPI](https://pypi.org/project/dflash-console/) |
| **dflash serve** | Stops a foreign Console on port 8900 (dev, EXE, or older pip) before starting |
| **Public preview** | GitHub Releases for the Windows installer; Discussions and Issues for feedback |

### v0.3.98 — pip install and extra terminal commands

| Feature | Description |
|---------|-------------|
| **pip package** | `pip install dflash-console` then `dflash serve`. The command is `dflash`. |
| **Terminal** | `dflash embed`, `dflash delete`, `dflash nodes`, and `dflash settings`. |

### v0.3.30 — choose vLLM in the toolbar

| Feature | Description |
|---------|-------------|
| **Engine menu** | Pick vLLM, Transformers, or DFlash on Engines, Playground, and Models before you load. |

### v0.3.29 — vLLM engine

| Feature | Description |
|---------|-------------|
| **vLLM** | Choose vLLM to load Hugging Face models. The engine downloads after install. The first-run wizard can install it on a new PC. |

### v0.3.28 — Windows installer

| Feature | Description |
|---------|-------------|
| **Desktop update** | Production installer with the terminal CLI, full local library list, source filters, and Downloads page. |

### v0.3.27 — full library list and source filters

| Feature | Description |
|---------|-------------|
| **dflash list** | Shows the same full local library as the Models tab. Filter with `--ollama`, `--lmstudio`, `--dflash`, or `--source`. |

### v0.3.26 — dflash works in PowerShell

| Feature | Description |
|---------|-------------|
| **dflash command** | Type `dflash list` in any PowerShell window. The installer registers the command so you do not need `.\dflash`. |

### v0.3.25 — terminal CLI

| Feature | Description |
|---------|-------------|
| **dflash command** | PowerShell can list models, check what is loaded, search, download, and chat with the running Console, the same way `ollama` talks to its server. |

### v0.3.24 — last downloads from this PC

| Feature | Description |
|---------|-------------|
| **Last downloads** | The Downloads page lists models already on this PC and lets you filter the last 24 hours, 7 days, 30 days, 90 days, or 12 months. |

### v0.3.6 — full mobile model names

| Feature | Description |
|---------|-------------|
| **Engine cards** | Narrow views keep the complete model name visible before the compact status details. |

### v0.3.4 — clearer catalog model variants

| Feature | Description |
|---------|-------------|
| **Catalog model cards** | Cards show approximate disk size beneath the update age and identify accelerator checkpoints, GGUF models, and full model weights at a glance. |

### v0.3.3 — higher sidebar restore control

| Feature | Description |
|---------|-------------|
| **Collapsed sidebar arrow** | The restore control is raised while preserving its original colors and appearance. |

### v0.3.2 — responsive engine cards

| Feature | Description |
|---------|-------------|
| **Portrait/split-window layout** | Engine cards switch to compact summaries at narrow browser widths; long names stay on one line while status, memory, token, and unload information remain visible. Desktop card layout is unchanged. |

### v0.3.0-dev — multi-modal runtime

| Feature | Description |
|---------|-------------|
| **Piper TTS** | Native bundle, Playground **Speak**, `POST .../v1/audio/speech`, per-runtime logs |
| **Whisper STT** | whisper.cpp `whisper-server` bundle, Playground **Transcribe**, proxied multipart transcriptions |
| **Embeddings** | Proxied `/v1/embeddings`, batch embed + `.jsonl` export, Playground **Embed** |
| **Runtime registry** | Adapter protocol/registry, `runtimes[]` config, shared port registry, `GET /api/runtimes` |
| **Process identity** | Path-specific tokens; `server.ps1` + supervisor clean up only Console-owned children |
| **Vision polish** | Inspector vision row; image attach gated on model capability |
| **Hardening** | Bundle manifests (`/api/runtimes/manifests`), sandbox/no-shell, per-adapter smoke tests |

### v0.2.9

| Feature | Description |
|---------|-------------|
| **Model catalog** | HF README titles/descriptions, lab filter, size/age on list cards, install detection, card download progress |
| **Download queue** | Global downloads tray, Models tab **Downloading** filter with live progress bars |
| **External API** | `/api/endpoints`, `/api/installed`, `/api/console/logs`, and request logging for integrations |
| **Playground** | Load models from catalog via engine + model picker |
| **UI polish** | Themed dropdowns, settings/docs as main views, terminology cleanup (checkpoint → model) |
| **Portable runtime** | Electron uses an external Console data root so the installer stays small and model files remain local |
| **Production boundary** | Loopback validation, path-safe model/projector handling, release preflight, and public repository security policy |

## Earlier improvements (v0.0.23)

| Feature | Description |
|---------|-------------|
| **Live token stats** | Loaded engine cards show generated tokens and speed; **Generating Xs** updates every second during inference |
| **Parallel loading** | Load multiple engines at once without blocking the UI |
| **Engine card UX** | Click a loaded card to open the runtime inspector; right-click for context menu (details, copy URL, unload, etc.) |
| **Accurate CPU meter** | System bar CPU reading matches Task Manager (process-time delta, not inflated WMI load) |
| **Cleaner load progress** | Solid progress bar without conflicting slide animation |
| **Locations settings** | One panel for config file, DFlash install, logs, presets, and console URL; import/export config and launch presets |
| **Clear engine logs** | One-click clear in the developer log header |
| **Chat proxy fix** | Long completions no longer freeze live stats polling |
| **In-app docs** | Documentation tab with overview, user guide, and full API catalog |

---

## Requirements

- **Windows** (primary target; PowerShell startup scripts)
- **Python 3.10+**
- **PowerShell 7+** (`pwsh`)
- **Node.js 22.12+** (Electron shell and packaging)
- Built **llama-server** and compatible model files in the configured `DFLASH_ROOT`
- NVIDIA GPU optional but recommended for multi-GPU load settings

---

## Quick start

### Install with pip

```powershell
pip install dflash-console
dflash serve
```

Open **http://127.0.0.1:8900/**. The PyPI package is `dflash-console`; the terminal
command is `dflash`. Model weights and `llama-server` stay on this PC
(`DFLASH_ROOT` or Settings). `pip install dflash` is a different project.

### From source

```powershell
git clone https://github.com/ilan4ever/Dflash-Console.git
cd Dflash-Console

# First-time setup
copy config.example.json config.json
# Edit config.json — set dflash_root, server ports, model paths, and enable a server profile.
# Keep model weights, logs, and local credentials out of Git.

.\server.ps1
```

The example server profile is disabled until you point it at a model available
on your machine. This prevents a fresh public checkout from trying to launch
excluded model assets.

Open **http://127.0.0.1:8900/** in your browser.

### Command line (like `ollama`)

The Console is the source of truth for models on this PC. `dflash list` shows
the same full library as the Models tab, including Ollama and LM Studio.

```powershell
pip install dflash-console
dflash serve
dflash help
dflash list
dflash list --ollama
dflash list --lmstudio
dflash list --dflash
dflash list --source library
dflash ps
dflash load qwen
dflash chat "hello"
dflash embed "index this"
dflash delete old-model
dflash nodes
dflash settings
dflash search qwen
```

From a checkout you can also run `.\dflash.ps1 install`.

| Command | What it does |
|---------|----------------|
| `dflash serve` / `open` | Start the UI / open the browser |
| `dflash list` / `ps` / `show` | Library, loaded models, one model |
| `dflash load` / `unload` / `start` / `stop` | Engines |
| `dflash chat` / `embed` | Prompt or vectors |
| `dflash delete` | Remove a local model from disk |
| `dflash nodes` | Other Consoles on your network |
| `dflash settings` | Show or change ports and paths |
| `dflash search` / `pull` / `downloads` | Hugging Face catalog |
| `dflash hardware` / `stats` / `report` / `logs` | This PC |
| `dflash api GET /api/health` | Any Console HTTP route |

Use `dflash <command> --help` for flags. `--json` prints raw server data. The
Console must be running (`dflash serve` or `.\server.ps1`) except for help,
version, serve, and install. Full command list: [docs/CLI.md](docs/CLI.md)
and the public site
[onevoiceai.in/dflash-console/docs](https://onevoiceai.in/dflash-console/docs/CLI.md).

`run.ps1` performs a full developer reset: it releases managed model VRAM,
stops configured engines, and starts a clean Console instance.

Foreground mode (attach logs to the terminal):

```powershell
.\server.ps1 -Foreground
```

Full restart (release GPU + stop engines):

```powershell
.\server.ps1 -Restart
```

Restart API after backend edits:

```powershell
.\scripts\restart-console-server.ps1
```

This is a gentle API restart. It preserves engine listeners, then the new
Console process adopts them and leaves router models unloaded. The UI
auto-refreshes when the API restarts (`/api/health` boot id).

### Desktop app (Electron)

The desktop shell opens the same Console UI in a native window and starts
the local API if it is not already running. The Windows installer is a
desktop shell; the Console data root remains separate so model weights and
llama-server binaries are not copied into every install. Set
`DFLASH_CONSOLE_ROOT` to a checkout containing `server.ps1`, `api`, and
`static`, or choose that folder when the packaged app first starts. The data
root still requires Python 3.10+ and PowerShell 7+.

```powershell
.\scripts\run-electron.ps1
```

Build Windows packages (branded DFlash setup + portable exe):

```powershell
.\scripts\run-electron.ps1 -Build
```

Output lands in `dist-electron/`. The shell uses the configured `ui_port`
(8900 by default) and talks to loopback, so the look and behavior match the
browser. Closing the window leaves the Console API and engines running, same
as closing a browser tab. Windows artifacts are not code-signed by default;
publishers should sign them before distributing outside a trusted team.

---

## Using the app

See **[docs/USER-GUIDE.md](./docs/USER-GUIDE.md)** for a full walkthrough, or open **Documentation → User guide** in the sidebar. The **About** page is available in both the browser UI and the Electron desktop app.

**Typical workflow:**

1. Open **Engines** and turn on an engine profile (toggle or **Load**).
2. Pick a model from the dropdown and click **Load**, or load from the **Models** tab.
3. Watch boot progress and live stats on the card; click the card for runtime settings in the side panel.
4. Point your app at the engine OpenAI URL shown on the card, or use the console proxy at `/api/servers/{id}/v1/chat/completions`.
5. Use the **Playground** mode switcher (**Chat · Speak · Transcribe · Embed**) for TTS, STT, and embedding workflows.
6. Use **Settings → Locations** to back up or restore `config.json` and launch presets.
7. Open **About** for the current release, developer attribution, license, and public project links.

---

## Third-party model libraries and trademarks

DFlash Console can scan model folders that the user explicitly enables, including
local LM Studio libraries. It reads and loads model files already present on the
user's machine; it does not include, copy, or redistribute the LM Studio
application or model weights.

“LM Studio library” identifies the detected folder source only. LM Studio is a
trademark of Element Labs, Inc.; DFlash Console is independent and is not
affiliated with or endorsed by LM Studio. Model files remain subject to the
licenses and terms provided by their respective model authors or distributors.
Users are responsible for checking those terms before copying, publishing, or
commercially deploying model files.

---

## Configuration

| Variable / file | Purpose |
|-----------------|--------|
| `config.json` | Local settings (not committed — copy from `config.example.json`) |
| `models_root` / `./models` | Developer model library inside the Console app folder |
| `DFLASH_ROOT_OVERRIDE` | Optional explicit override for the configured DFlash root |
| `config.json` → `dflash_root` | Path to DFlash repo with llama-server binaries and launch scripts |
| `config.json` → `servers[]` | llama-server / DFlash engine profiles: port, GPU layers, context, idle unload |
| `config.json` → `runtimes[]` | Non-llama runtimes (Piper, STT): `runtime_id`, port, `device_policy` |
| `config.json` → `model_libraries[]` | Folders scanned for local models and HF download targets |

Default UI port is **8900**. Engine ports (e.g. 8090, 8092) are configured per server block.

---

## Security boundary

The Console binds to loopback by default and validates configured engine URLs
as loopback-only. It is designed for one trusted user on one machine; it does
not provide multi-user authentication or CSRF protection. Do not expose the
Console or engine ports to a LAN, reverse proxy, or public network without
adding an authenticated access layer.

Hugging Face downloads use `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` when private
repositories require them. Keep those values in the process environment, never
in `config.json` or committed files.

---

## Project layout

```
Dflash-Console/
├── api/app.py              # FastAPI routes
├── core/                   # Config, runtime, model discovery, HF, GPU, inference stats
│   └── runtimes/           # Adapter registry + protocol (piper, stt, noop, contention)
├── runtimes/               # Installed engine bundles (piper/, stt/) + manifests (gitignored)
├── static/                 # UI (HTML, CSS, JS)
├── electron/               # Desktop shell (Electron)
├── scripts/                # Server start / restart / Electron helpers
├── tests/                  # pytest suite
├── dflash_cli/             # `dflash` terminal command
├── pyproject.toml          # pip package `dflash-console`
├── docs/
│   ├── USER-GUIDE.md       # End-user walkthrough
│   ├── CLI.md              # Full terminal command list
│   ├── ARCHITECTURE.md     # Public architecture overview
│   ├── LICENSING.md        # AGPL and redistribution guide
│   ├── RELEASING.md        # GitHub and Windows release process
│   └── ui/                 # UI panel design notes
├── run.ps1                 # Full reset and background API startup
├── package.json            # Electron desktop packaging
├── config.example.json     # Template configuration
├── CONTRIBUTING.md         # Contribution workflow and AGPL terms
├── CODE_OF_CONDUCT.md      # Community standards
├── SUPPORT.md              # Questions, bugs, and contact channels
├── SECURITY.md             # Vulnerability reporting
├── TRADEMARKS.md           # DFlash name and logo policy
├── NOTICE.md               # Copyright and third-party notices
└── LICENSE                 # GNU AGPL v3 or later
```

---

## API (selected)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Liveness + `boot_id` (UI reload watch) |
| `GET` | `/api/servers` | All engine statuses + inference stats |
| `POST` | `/api/servers/{id}/load` | Load model (optional runtime JSON body) |
| `POST` | `/api/servers/{id}/unload` | Eject model (router stays up) |
| `POST` | `/api/servers/{id}/v1/chat/completions` | Proxy chat; updates live token stats |
| `POST` | `/api/servers/{id}/v1/embeddings` | Text → vectors (embedding profile, OpenAI shape) |
| `POST` | `/api/servers/{id}/embed/batch` | Batch embed text items; optional `.jsonl` export |
| `GET` | `/api/runtimes` | Unified runtime list (`servers[]` + `runtimes[]` + adapters) |
| `GET` | `/api/runtimes/manifests` | Installed runtime plugin manifests + process tokens |
| `POST` | `/api/runtimes/piper/v1/audio/speech` | Text → speech (Piper, OpenAI shape) |
| `POST` | `/api/runtimes/stt/v1/audio/transcriptions` | Audio → text (whisper.cpp, OpenAI shape) |
| `GET` | `/api/models` | Full local library (same as Models tab). `source=ollama\|lmstudio\|dflash\|library` |
| `DELETE` | `/api/models/file` | Delete a local model file or Hugging Face folder |
| `GET` / `POST` / `DELETE` | `/api/nodes` | Remote Console nodes |
| `GET` / `PUT` | `/api/config` | Settings (`dflash settings`) |
| `GET` | `/api/hf/downloads` | Current downloads and last-download history |
| `POST` | `/api/models/load` | Unified loader — load ANY catalog model by path; dispatches by modality |
| `GET` | `/api/gateway` | Console OpenAI gateway status (port, url, running, default server, routes) |
| `GET` | `/api/docs/catalog` | In-app documentation JSON |
| `GET` | `/api/model-libraries/scan` | PC scan for model folders |
| `GET` | `/api/fs/browse` | Folder picker for library paths |
| `GET` | `/api/hf/search` | Hugging Face model search |
| `POST` | `/api/hf/download` | Download GGUF into a library |
| `POST` | `/api/hf/install` | Search → download → load in one call |
| `GET` | `/api/status/loaded` | Currently loaded models (engines + runtimes) |
| `GET` | `/api/status/report` | Full report: CPU/RAM/VRAM, engines, loaded models |
| `DELETE` | `/api/logs/{id}` | Clear engine log file |

Full route list: `api/app.py` or **Documentation** tab in the UI.

---

## Development

```powershell
pip install -e .[dev]
pip install -r requirements-dev.txt
pytest
dflash serve
```

Cursor agents: see `.cursor/rules/dev-server-restart.mdc` — restart the API after Python backend changes.

---

## Related projects

- **DFlash** — speculative decoding stack and llama-server profiles this console manages
- Model weights and native build outputs are intentionally excluded from this repository.

---

## Community and support

Use the repository's public channels so questions and fixes are easy for other
users to find:

- **Questions and setup help:** [GitHub Discussions](https://github.com/ilan4ever/Dflash-Console/discussions)
- **Bug reports:** [Issue tracker](https://github.com/ilan4ever/Dflash-Console/issues/new?template=bug_report.yml)
- **Feature ideas:** [Feature request](https://github.com/ilan4ever/Dflash-Console/issues/new?template=feature_request.yml)
- **Security vulnerabilities:** follow [SECURITY.md](./SECURITY.md) and use
  GitHub's private reporting channel; do not post exploit details publicly.
- **Developer:** [ILAN AVIV](https://github.com/ilan4ever)

Please include the Console version from **About**, Windows and Python versions,
the relevant logs with secrets removed, and a minimal reproduction. Never
include `config.json`, access tokens, model weights, or private logs.

---

## License

This project is licensed under the **GNU Affero General Public License
version 3 or later (AGPL-3.0-or-later)**. See [LICENSE](./LICENSE).

AGPL-covered source may be used, studied, modified, and redistributed under
the license terms. If you distribute a modified version, or run a modified
version as a network service, the AGPL source-sharing obligations apply. The
DFlash name, logo, and other marks are not granted by the software license;
see [TRADEMARKS.md](./TRADEMARKS.md). Third-party runtimes, binaries, model
weights, and other assets remain under their own licenses; see
[NOTICE.md](./NOTICE.md).
The AGPL migration begins with the `0.3.0` release; earlier versions retain
the license that accompanied their distribution.

---

## Credits

DFlash Console is developed and maintained by **ILAN AVIV**. The project is
free software under the GNU AGPL v3 or later. See the [developer profile](https://github.com/ilan4ever),
[source repository](https://github.com/ilan4ever/Dflash-Console), and related
[DFlash project](https://github.com/ilan4ever/Dflash).

When you redistribute or build on DFlash Console, retain the copyright and
license notices and provide the corresponding source as required by the AGPL.
Please also include a link to the
[DFlash Console source repository](https://github.com/ilan4ever/Dflash-Console)
in your README, About page, or other project documentation.

---

## Roadmap

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the public architecture
overview. Run `python scripts/release-preflight.py` before packaging a
checkout. Public release discussions and roadmap items belong in the
[GitHub issue tracker](https://github.com/ilan4ever/Dflash-Console/issues).
