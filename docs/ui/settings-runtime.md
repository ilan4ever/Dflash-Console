# Settings — Runtime limits

The current runtime panel controls the local service boundary and engine
behavior. It does not download or update llama.cpp runtime packs.

## Network & API

- Console UI port, default `8900`
- Loopback Console URL
- Managed engine host and per-engine ports
- OpenAI-compatible engine and Console proxy routes

Configured URLs are validated as loopback-only by the backend.

## Console OpenAI gateway

**Settings → Engine profiles → Console OpenAI gateway** configures the
OpenAI-compatible gateway (see [USER-GUIDE §8 — gateway](../USER-GUIDE.md)):

- **Gateway port** (`gateway_port`, default `8001`) — the friendly port apps
  point at: `http://127.0.0.1:8001/v1`. Must differ from the Console UI port
  and is excluded from other engine/runtime port allocation
  (`reserved_ports()`).
- **Default chat engine** (`gateway_server_id`) — which enabled engine serves
  `/v1/chat/completions`; falls back to the first enabled non-embedding engine.

The gateway accepts **any model name** and rewrites it to the resolved engine's
actual checkpoint id, so clients can send the engine id, the model id, or any
alias. Changing the port takes effect after the console restarts (the gateway
starts/stops with the Console process).

## Runtime limits

- Idle unload behavior for managed engines
- Engine lifecycle behavior used when loading, unloading, or restarting
- Runtime values passed through the per-engine inspector

## Launch preset

Import and export `.ini` launch preset files from the configured presets
folder. Native llama-server binaries are selected from the configured DFlash
root; the Console does not manage remote runtime downloads.
