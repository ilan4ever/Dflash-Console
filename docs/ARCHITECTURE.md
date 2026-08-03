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
                    | PowerShell process control
                    v
             llama-server engines
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
