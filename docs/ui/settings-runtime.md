# Settings — Runtime limits

The current runtime panel controls the local service boundary and engine
behavior. It does not download or update llama.cpp runtime packs.

## Network & API

- Console UI port, default `8900`
- Loopback Console URL
- Managed engine host and per-engine ports
- OpenAI-compatible engine and Console proxy routes

Configured URLs are validated as loopback-only by the backend.

## Runtime limits

- Idle unload behavior for managed engines
- Engine lifecycle behavior used when loading, unloading, or restarting
- Runtime values passed through the per-engine inspector

## Launch preset

Import and export `.ini` launch preset files from the configured presets
folder. Native llama-server binaries are selected from the configured DFlash
root; the Console does not manage remote runtime downloads.
