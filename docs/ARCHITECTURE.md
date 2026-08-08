# Architecture

DFlash Console is a local control plane for llama.cpp-compatible inference
engines. It combines a FastAPI service with a static HTML/CSS/JavaScript UI.
The optional Electron shell opens the same UI and starts the local service.
The project is developed and maintained by **ILAN AVIV** under the MIT License.

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
            |         |        |
      llama-server  Piper    whisper-server
      (existing)   (CLI)     (STT)
```

- `api/` exposes health, configuration, model, download, runtime, and proxy
  routes.
- `core/` contains configuration validation, model discovery, Hugging Face
  integration, engine lifecycle, GPU inspection, and inference statistics.
- `static/` contains the browser UI, in-app documentation, and About page.
- `scripts/` contains Windows startup, restart, release, and engine helpers.
- `electron/` contains the sandboxed desktop shell.

## Runtime roots

The Console data root contains the backend, UI, configuration, scripts, and
local runtime state. `DFLASH_ROOT` may point to a separate engine tree when
the native llama.cpp build or model storage is managed elsewhere. Model
weights, logs, and credentials are intentionally not part of the repository.

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
| `/api/servers/{id}/v1/chat/completions` | llama-server |
| `/api/runtimes/piper/v1/audio/speech` | Piper CLI (stdin → WAV stdout) |
| `/api/runtimes/stt/v1/audio/transcriptions` | whisper-server `/inference` |
| `/api/servers/{id}/v1/embeddings` | llama-server embedding profile |
| `/api/servers/{id}/embed/batch` | llama-server embedding profile (batch + jsonl) |

### Process identity

Each adapter contributes path-specific identity tokens to the shared registry.
`managed_process_identity()` (server_boot), the port kill-listener, restart
adoption, and `server.ps1` `Stop-ListenersOnPort` all use the same token set, so
cleanup recognises Console-managed children (Piper, whisper-server) without
adopting foreign processes from other applications.

### Runtime bundles & manifests

Engines and voices live outside the installer under the Console data root
(`runtimes/piper/`, `runtimes/stt/`). Each adapter writes a `manifest.json` at
boot; `GET /api/runtimes/manifests` aggregates them for diagnostics and the
Settings/Repair UI.

## Security model

The default trust boundary is one user on one machine. The Console binds to
loopback and validates model/projector paths against configured model roots.
It is not a multi-user service. Authentication and CSRF protection are
required before exposing it beyond loopback.

## Validation

The minimum release checks are:

```powershell
pytest -q
python scripts/release-preflight.py
node --check electron/main.js
npm audit --audit-level=high
```
