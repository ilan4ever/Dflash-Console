# DFlash Console

**Local control panel for DFlash speculative-decoding stacks** — manage llama-server engines, model libraries, Hugging Face downloads, and GPU settings from a single web UI.

> **Status:** Public preview. This project is intended for local, single-user Windows deployments.

**Developer:** ILAN AVIV · **UI:** [http://127.0.0.1:8900/](http://127.0.0.1:8900/) · **Version:** v0.0.30

---

## What it does

DFlash Console is a standalone FastAPI + vanilla JavaScript app that sits beside your [DFlash](https://github.com/ilan4ever/Dflash) installation. It gives you an LM Studio–style workbench focused on **your** engine profiles—not a generic chat client.

| Area | Highlights |
|------|------------|
| **Engines** | Start/stop llama-server routers, load & eject models in parallel, live boot progress, token stats on cards, developer logs with clear button |
| **Models** | Scan local GGUF, Piper, Whisper, OCR, and embedding folders across multiple library roots |
| **Model catalog** | Browse and download Hugging Face models into configured library locations |
| **Settings** | GPU strategy, model storage, engine network/API, MCP client preview, **Locations** panel with config/preset import-export |
| **Documentation** | In-app API reference, runtime JSON shapes, user guide, and release notes |
| **About** | Developer attribution, version, license, runtime boundary, and public project links |
| **Discovery** | **Scan PC** finds model folders; **Add folder** opens a drive-aware browser (C:, D:, …) |

Router mode uses `--models-preset` with load/unload over HTTP so engines stay listening while models swap.

---

## Recent improvements (v0.0.30)

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

Build Windows packages (NSIS installer + portable exe):

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
5. Use **Settings → Locations** to back up or restore `config.json` and launch presets.
6. Open **About** for the current release, developer attribution, license, and public project links.

---

## Configuration

| Variable / file | Purpose |
|-----------------|--------|
| `config.json` | Local settings (not committed — copy from `config.example.json`) |
| `models_root` / `./models` | Developer model library inside the Console app folder |
| `DFLASH_ROOT_OVERRIDE` | Optional explicit override for the configured DFlash root |
| `config.json` → `dflash_root` | Path to DFlash repo with llama-server binaries and launch scripts |
| `config.json` → `servers[]` | Engine profiles: port, GPU layers, context, idle unload |
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
├── static/                 # UI (HTML, CSS, JS)
├── electron/               # Desktop shell (Electron)
├── scripts/                # Server start / restart / Electron helpers
├── tests/                  # pytest suite
├── docs/
│   ├── USER-GUIDE.md       # End-user walkthrough
│   ├── ARCHITECTURE.md     # Public architecture overview
│   ├── RELEASING.md        # GitHub and Windows release process
│   └── ui/                 # UI panel design notes
├── run.ps1                 # Full reset and background API startup
├── package.json            # Electron desktop packaging
├── config.example.json     # Template configuration
├── CONTRIBUTING.md         # Contribution workflow
├── SUPPORT.md              # Questions, bugs, and contact channels
├── SECURITY.md             # Vulnerability reporting
└── LICENSE                 # MIT license
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
| `GET` | `/api/models` | Local catalog from enabled libraries |
| `GET` | `/api/docs/catalog` | In-app documentation JSON |
| `GET` | `/api/model-libraries/scan` | PC scan for model folders |
| `GET` | `/api/fs/browse` | Folder picker for library paths |
| `GET` | `/api/hf/search` | Hugging Face model search |
| `POST` | `/api/hf/download` | Download GGUF into a library |
| `DELETE` | `/api/logs/{id}` | Clear engine log file |

Full route list: `api/app.py` or **Documentation** tab in the UI.

---

## Development

```powershell
pip install -r requirements.txt
pip install -r requirements-dev.txt
pytest
.\run.ps1 -Foreground
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

This project is licensed under the MIT License. See [LICENSE](./LICENSE).

---

## Credits

DFlash Console is developed and maintained by **ILAN AVIV**. The project is
open source under the MIT License. See the [developer profile](https://github.com/ilan4ever),
[source repository](https://github.com/ilan4ever/Dflash-Console), and related
[DFlash project](https://github.com/ilan4ever/Dflash).

---

## Roadmap

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the public architecture
overview. Run `python scripts/release-preflight.py` before packaging a
checkout. Public release discussions and roadmap items belong in the
[GitHub issue tracker](https://github.com/ilan4ever/Dflash-Console/issues).
