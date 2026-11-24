# Architecture

DFlash Console is a local control plane for llama.cpp-compatible inference
engines. It combines a FastAPI service with a static HTML/CSS/JavaScript UI.
The optional Electron shell opens the same UI and starts the local service.
The project is developed and maintained by **ILAN AVIV** under the GNU Affero
General Public License version 3 or later. See the repository
[LICENSE](../LICENSE) and [TRADEMARKS.md](../TRADEMARKS.md).

## Components

```text
Browser or Electron shell
            |
            | HTTP on loopback
            v
      FastAPI Console
       /     |       \
 config   catalog   runtime
                    |
        runtime supervisor (adapters)
            |         |        |         |          |
      llama-server  vLLM   Transformers  FreeToken  Piper / whisper
      DFlash 1/2    HF      HF           WSL2       TTS / STT
```

- `api/` exposes health, configuration, model, download, runtime, and proxy
  routes. `GET /api/models` is the full local library; `source=` filters
  Ollama, LM Studio, DFlash, or library files.
- `core/` contains configuration validation, model discovery, Hugging Face
  integration, engine lifecycle, GPU inspection, and inference statistics.
- `dflash_cli/` is the `dflash` command (`pip install dflash-console`).
  `dflash list`, `embed`, `delete`, `nodes`, and `settings` talk to the same
  local API as the UI.
- `static/` contains the browser UI, in-app documentation, and About page.
- `scripts/` contains Windows startup, restart, release, and engine helpers.
- `electron/` contains the sandboxed desktop shell.

## Runtime roots

The Console data root contains the backend, UI, configuration, scripts, and
local runtime state. A pip install ships the Python packages plus `static/`,
`assets/`, and `docs/`. Writable config and logs then live under
`DFLASH_CONSOLE_ROOT` or `%LOCALAPPDATA%\DFlash Console`. `DFLASH_ROOT` may
point to a separate engine tree when the native llama.cpp build or model
storage is managed elsewhere. Model weights, logs, and credentials are
intentionally not part of the repository.

The Electron installer is a thin shell. A packaged launch uses
`DFLASH_CONSOLE_ROOT` or asks the user to select a Console data root. This
keeps large model files and native binaries out of the installer.

The browser and Electron UI are the same application. Documentation and About
content are served from the selected Console data root, so both surfaces show
the same release metadata and runtime behavior.

## Engine lifecycle

1. Configuration is loaded and validated before startup.
2. Engine profiles are checked for loopback addresses and valid ports.
3. The Console starts or adopts local llama-server listeners.
4. Router profiles load and unload models through the engine HTTP API.
5. The UI polls health and runtime state, then reloads after an API boot ID
   changes.

## Runtime adapters (multi-modal)

`core/runtimes/` is a small adapter registry that lets the Console run non-llama
modalities alongside llama-server:

- `base.py` — `RuntimeAdapter` protocol (`runtime_id`, `modalities`,
  `execution_mode` `server|cli`, `process_identity_tokens`,
  `health/start/stop/load/unload`, `openai_routes`).
- `registry.py` — adapter registry, shared process-identity tokens, and the
  `runtimes/process-tokens.json` manifest read by `server.ps1`.
- `noop.py` — no-op adapter so the UI can list adapters before any runtime ships.
- `piper.py` — Piper TTS (CLI: text → WAV via the native binary).
- `stt.py` — whisper.cpp `whisper-server` (server mode: multipart `/inference`).
- `freetoken.py` — FreeToken `ft serve` through WSL2 (server mode, loopback
  OpenAI API, Windows-to-WSL model-path conversion).
- `vllm.py` / `transformers_hf.py` — on-demand Hugging Face SafeTensors engines.
- `core/stack_match.py` and `core/dflash_generation.py` — DFlash 1 vs DFlash 2
  draft discovery, architecture preflight, and attach.
- `contention.py` — GPU contention / stop-others scaffold (external apps are
  warned by name; the Console only claims to kill its own children).

`servers[]` stays the persistent shape for llama-server / DFlash / GGUF
embeddings / vision. `runtimes[]` is for non-llama adapters only; there is no
one-shot migration between the two.

### Console proxies OpenAI-shaped routes

Children listen on internal loopback ports only. The Console translates
OpenAI-shaped client requests to each child's native API:

| Route | Child |
|-------|-------|
| `/api/servers/{id}/v1/chat/completions` | llama-server / DFlash 1 / DFlash 2 |
| `/api/servers/vllm/v1/chat/completions` | vLLM (when installed and loaded) |
| `/api/servers/transformers/v1/chat/completions` | Transformers (when installed and loaded) |
| `/api/servers/freetoken/v1/chat/completions` | FreeToken `ft serve` in WSL2 |
| `/api/runtimes/piper/v1/audio/speech` | Piper CLI (stdin → WAV stdout) |
| `/api/runtimes/stt/v1/audio/transcriptions` | whisper-server `/inference` |
| `/api/servers/{id}/v1/embeddings` | llama-server embedding profile |
| `/api/servers/{id}/embed/batch` | llama-server embedding profile (batch + jsonl) |

### Process identity

Each adapter contributes path-specific identity tokens to the shared registry.
`managed_process_identity()` (server_boot), the port kill-listener, restart
adoption, and `server.ps1` `Stop-ListenersOnPort` all use the same token set, so
cleanup recognises Console-managed children (Piper, whisper-server, and the
FreeToken WSL launcher) without
adopting foreign processes from other applications.

### Runtime bundles & manifests

Engines and voices live outside the installer under the Console data root
(`runtimes/piper/`, `runtimes/stt/`, `runtimes/freetoken/`). Each adapter writes
a `manifest.json` at boot; `GET /api/runtimes/manifests` aggregates them for
diagnostics and the Settings/Repair UI. FreeToken's large Linux environment
is installed inside WSL and its manifest records the distro and `ft` executable.

## Security model

The trust boundary is one owner. The Console and the OpenAI gateway still
listen only on loopback (`127.0.0.1`), and model/projector paths are validated
against the configured model roots. Engine children (llama-server, FreeToken in
WSL2, and the rest) are started with a loopback bind. Sharing on the local
network is planned in [LAN-SHARING-AND-AUTH-PLAN.md](./LAN-SHARING-AND-AUTH-PLAN.md);
sign-in and machine keys (Phase 1) are already enforced, so they are in place
before any network access.

### Who may call what

`core/access_control.py` is an ASGI middleware on both the Console (`:8900`)
and the gateway (`:8001`). Every route is listed in one table there; a test
fails when a new route is not classified, and an unlisted route is treated as
local-only.

| Caller | How it is recognized | What it may do |
|---|---|---|
| This PC | Loopback address (`127.0.0.0/8`, `::1`) and no proxy forwarding header | Everything, with no sign-in, exactly as before |
| Owner, signed in from a browser | `dflash_session` cookie (HttpOnly, SameSite=Strict) | Everything except local-only routes; every POST/PUT/PATCH/DELETE also needs `X-CSRF-Token` and a matching `Origin` |
| Another Console | `Authorization: Bearer dfk_…` machine key | Only GPU-sharing routes: status, load, unload, inference. Downloads only with the key's "May download models here" setting |
| Anyone else | — | 401 (the sign-in page for a browser) |

A request relayed by a reverse proxy on this PC (`tailscale serve`, cloudflared,
nginx) arrives from loopback but carries `X-Forwarded-For` or a similar header;
it counts as another computer. An SSH tunnel carries no such header, so a
tunneled request counts as this PC, as before.

**Local-only routes** answer 403 to any other computer, even with a valid
session or key: shutdown, file browsing and file operations outside the model
libraries, config import and export, engine install and uninstall, and anything
that registers or runs a program. Some routes refuse only certain fields from
another computer: `PUT /api/config` (engine binaries, engine and runtime
definitions, model libraries, SSH nodes, API keys, ports, automatic engine
updates) and `POST`/`PATCH /api/nodes` (SSH tunnel settings, which make the
Console run `ssh`). `GET /api/config` hides the keys this Console uses for other
Consoles from another computer.

**Machine keys** are 256-bit random values (`dfk_…`), shown once when created and
stored as SHA-256 hashes. A key never opens a browser session. With "May
download models here" on, a key may download only files the Hugging Face Hub
lists in an `owner/name` repository (never another URL or a path outside the
repository), within the key's disk limit, counted over every download it
started. A key sees only this PC's own GPUs, not GPUs this Console borrows from
other nodes.

**Owner password** is optional while the Console is loopback-only. It is stored
as an scrypt hash (`hashlib.scrypt`, N=2¹⁷, r=8, p=1) in a `users` list with a
single `owner` entry. Wrong passwords slow down, then lock sign-in for five
minutes (per address, and for everyone after many failures). Changing the
password signs out every other browser; removing it signs out all of them.

**Storage.** Password, session, and key hashes live in `auth.json` next to
`config.json`, not in `config.json` itself, so `GET /api/config`, config export,
presets, and the diagnostics bundle never include them. Settings → Sign-in &
keys manages all of it.

## Validation

The minimum release checks are:

```powershell
pytest -q
python scripts/release-preflight.py
node --check electron/main.js
npm audit --audit-level=high
```
