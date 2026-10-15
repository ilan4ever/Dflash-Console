# External API reference

DFlash Console exposes two loopback APIs. Use the Console API for lifecycle and
hardware control, and use the OpenAI-compatible gateway for normal AI client
traffic.

```text
Console control API: http://127.0.0.1:8900
OpenAI gateway:      http://127.0.0.1:8001/v1
```

The ports can be changed in `config.json`. Discover the exact ports and every
route at runtime:

```http
GET http://127.0.0.1:8900/api/endpoints
GET http://127.0.0.1:8900/openapi.json
GET http://127.0.0.1:8900/api/docs/catalog
GET http://127.0.0.1:8900/api/gateway
```

`/api/endpoints` includes a `gateway` section, so an external application does
not need to hard-code the gateway port or guess which routes are available.
Swagger is available at `/docs` and the gateway has its own `/openapi.json`.

## Request identity

Send `X-DFlash-Client` on every load, chat, embedding, or runtime request. The
value is shown on the Engines card and in API logs. Optional component headers
are also forwarded:

```http
X-DFlash-Client: MyApp
X-DFlash-Component: myapp.worker
X-DFlash-Role: chat
```

## Gateway API: OpenAI-compatible traffic

Base URL: `http://127.0.0.1:8001/v1`

| Method | Route | Use |
|---|---|---|
| GET | `/v1/models` | All public local, runtime, embedding, and configured cloud model IDs |
| POST | `/v1/chat/completions` | Chat; supports streaming SSE and first-request JIT loading |
| POST | `/v1/embeddings` | Embeddings through the configured embedding engine |
| POST | `/v1/audio/speech` | Piper text-to-speech, returns WAV |
| POST | `/v1/audio/transcriptions` | Whisper speech-to-text, multipart `file=audio` |
| GET | `/health` | Gateway and Console liveness |
| GET | `/` | Gateway capabilities and URL hints |

Example chat:

```powershell
$body = @{
  model = 'underdog-saluki-27b-1.0-iq2-mix'
  messages = @(@{ role = 'user'; content = 'Hello' })
  max_tokens = 256
  stream = $false
} | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8001/v1/chat/completions `
  -Method Post -ContentType 'application/json' `
  -Headers @{ 'X-DFlash-Client' = 'MyApp' } -Body $body
```

The gateway accepts a configured server ID, canonical model ID, dotted model
ID, hyphenated model ID, or a registered alias. It does not silently substitute
a different selected model. Use `X-DFlash-Strict-Model: 1` when a mismatch must
be rejected with HTTP 409.

## Console control API

Base URL: `http://127.0.0.1:8900`

### Health, discovery, reports, and logs

| Method | Route | Use |
|---|---|---|
| GET | `/api/health` | Version, boot ID, UI URL, setup state |
| GET | `/api/endpoints` | Live Console and gateway route inventory |
| GET | `/api/gateway` | Gateway URL, running state, default server, and route list |
| GET | `/api/system-stats` | CPU, RAM, GPU utilization, and VRAM totals |
| GET | `/api/status/loaded` | Loaded model inventory |
| GET | `/api/status/report` | Full machine/runtime/engine report; `include_external=false` omits external-app scan |
| GET | `/api/gpu/contention` | GPU processes and other-app contention |
| GET | `/api/gpu/budget` | Free VRAM, reserve policy, loaded engines, and guardrail inputs |
| GET | `/api/console/logs?tail=200&errors_only=true` | Structured Console, engine, startup, and API-call logs |
| GET | `/api/diagnostics/bundle?output=json` | Redacted diagnostic bundle |
| GET | `/api/diagnostics/bundle?output=text` | Human-readable diagnostic report |
| POST | `/api/diagnostics/report` | Build a report with `user_note`, `reproduction`, and optional private upload |
| GET | `/api/release-notice` | Newer release notice for cloned/pip installs |

### Engines and model lifecycle

Replace `{server_id}` with the ID returned by `GET /api/servers`.

| Method | Route | Use |
|---|---|---|
| GET | `/api/servers` | All configured engines with status, loaded IDs, GPU, and readiness |
| GET | `/api/servers/{server_id}/status` | One engine status |
| GET | `/api/servers/{server_id}/chat-ready` | Authoritative chat readiness and retry reason |
| GET | `/api/servers/{server_id}/load-plan` | VRAM estimate, already-loaded state, and `unload_first` suggestions |
| PATCH | `/api/servers/{server_id}` | Update port, context, GPU index, load settings, and inference settings |
| POST | `/api/servers/{server_id}/listen` | Start an idle router without loading weights |
| POST | `/api/servers/{server_id}/engine/start` | Alias for `listen` |
| POST | `/api/servers/{server_id}/load` | Load the configured or requested checkpoint |
| POST | `/api/servers/{server_id}/unload` | Unload weights while keeping the listener when supported |
| POST | `/api/servers/{server_id}/start` | Start/load an engine through the generic lifecycle path |
| POST | `/api/servers/{server_id}/stop` | Stop the engine process |
| POST | `/api/servers/{server_id}/engine/stop` | Alias for `stop` |
| POST | `/api/servers/{server_id}/reload` | Stop and restart using saved settings |
| POST | `/api/servers/{server_id}/cancel-inference` | Cancel active inference |
| GET | `/api/servers/{server_id}/inference-stats` | Current/last inference counters |
| GET | `/api/logs/{server_id}?tail=200` | Engine log tail |
| DELETE | `/api/logs/{server_id}` | Clear one engine log |

Load example:

```json
{
  "model_path": "C:\\models\\model.gguf",
  "model_id": "my-model",
  "gpu_device": "auto",
  "context_size": 32768,
  "load_settings": {"gpu_layers": 99, "flash_attention": true},
  "inference_settings": {"temperature": 0.7, "max_tokens": 4096}
}
```

Chat through one engine directly:

```http
POST /api/servers/{server_id}/v1/chat/completions
POST /api/servers/{server_id}/v1/embeddings
POST /api/servers/{server_id}/embed/batch
```

The direct engine chat route performs readiness/JIT loading. Poll
`/api/servers/{server_id}/chat-ready` when an external app wants a progress UI.

### Catalog, files, and downloads

| Method | Route | Use |
|---|---|---|
| GET | `/api/models` | Full local catalog; `source`, `quick`, and `refresh` filters |
| POST | `/api/models/load` | Load a catalog row by `path`, `model_id`, `server_id`, or `runtime_id` |
| POST | `/api/models/auto-register` | Register a discovered model |
| POST | `/api/models/auto-setup` | Register/setup a discovered model stack |
| POST | `/api/models/import-into-console` | Import an external model into Console ownership |
| DELETE | `/api/models/file` | Delete a permitted model file/folder |
| POST | `/api/models/cleanup` | Clean safe stale model artifacts |
| GET | `/api/models/import-progress/{progress_id}` | Import progress |
| GET | `/api/model-libraries/scan` | Scan configured libraries |
| GET | `/api/model-libraries/preview` | Preview a library path |
| GET | `/api/model-libraries/import-plan` | Plan an import |
| POST | `/api/model-libraries/import` | Import a library |
| GET | `/api/fs/browse` | Browse permitted filesystem roots |
| POST | `/api/fs/reveal` | Reveal a permitted path |
| GET | `/api/hf/search` | Search Hugging Face |
| GET | `/api/hf/models/{repo_id}` | Model detail and files |
| GET | `/api/hf/files` | Repository files |
| GET | `/api/hf/readme` | Repository README |
| GET | `/api/hf/repo-sizes` | Repository size estimates |
| GET | `/api/hf/local-match` | Match a Hub model to local files |
| GET | `/api/hf/local-installs` | Local installs for a Hub repository |
| POST | `/api/hf/download` | Start a file/repository download |
| GET | `/api/hf/download/{job_id}` | Download status |
| GET | `/api/hf/downloads` | Current/history downloads |
| POST | `/api/hf/downloads/resume` | Resume pending downloads |
| POST | `/api/hf/downloads/{job_id}/resume` | Resume one job |
| DELETE | `/api/hf/downloads/{job_id}` | Clear one completed job |
| DELETE | `/api/hf/downloads` | Clear completed history |
| POST | `/api/hf/install` | Search/download and optionally load a model |

### Hardware, runtimes, providers, and nodes

| Method | Route | Use |
|---|---|---|
| GET/PATCH | `/api/hardware` | Hardware policy and enabled GPUs |
| GET | `/api/gpu-devices` | Detected GPUs with stable indices |
| GET | `/api/remote-gpus` | Shared GPUs |
| POST | `/api/remote-gpus/load` | Load on a shared GPU |
| GET | `/api/runtimes` | Runtime adapters and health |
| GET | `/api/runtimes/manifests` | Runtime bundle manifests |
| GET | `/api/runtimes/{runtime_id}` | One runtime |
| GET | `/api/runtimes/{runtime_id}/voices` | TTS voices |
| POST | `/api/runtimes/{runtime_id}/install` | Install an on-demand runtime |
| POST | `/api/runtimes/{runtime_id}/uninstall` | Uninstall an on-demand runtime |
| POST | `/api/runtimes/{runtime_id}/load` | Load a runtime |
| POST | `/api/runtimes/{runtime_id}/unload` | Unload a runtime |
| POST | `/api/runtimes/{runtime_id}/start` | Start a runtime |
| POST | `/api/runtimes/{runtime_id}/stop` | Stop a runtime |
| GET | `/api/runtimes/{runtime_id}/logs` | Runtime logs |
| POST | `/api/runtimes/{runtime_id}/v1/audio/speech` | Direct TTS |
| POST | `/api/runtimes/{runtime_id}/v1/audio/transcriptions` | Direct STT |
| GET/PUT/DELETE | `/api/api-providers` | Manage configured external OpenAI-compatible providers |
| POST | `/api/api-providers/fetch-models` | Fetch provider model IDs before saving |
| POST | `/api/api-providers/{provider_id}/test` | Test credentials/base URL |
| GET | `/api/nodes` | Remote Console nodes |
| GET | `/api/nodes/connect/wizard` | Connection wizard defaults |
| POST | `/api/nodes/connect/test` | Test a node connection |
| POST | `/api/nodes/connect/ssh-command` | Build an SSH command |
| POST | `/api/nodes` | Add a node |
| PATCH | `/api/nodes/{node_id}` | Update a node |
| DELETE | `/api/nodes/{node_id}` | Delete a node |
| POST | `/api/nodes/{node_id}/health` | Probe a node |
| POST | `/api/nodes/{node_id}/v1/chat/completions` | Proxy chat to a node |

### DFlash stacks and presets

| Method | Route | Use |
|---|---|---|
| GET | `/api/stacks/capable-targets` | Targets that can accept a draft |
| GET | `/api/stacks/preflight` | Validate a target/draft pair |
| GET | `/api/stacks/match` | Match a target to a draft |
| POST | `/api/stacks/find-draft` | Find a compatible draft |
| POST | `/api/stacks/find-and-attach-draft` | Find and attach a draft |
| POST | `/api/stacks/{server_id}/replace-draft` | Replace a server draft |
| POST | `/api/stacks/ensure` | Ensure a server stack |
| GET | `/api/stacks/suggest-port` | Suggest a free engine port |
| GET/POST | `/api/servers/profiles` | Inspect server profiles |
| POST | `/api/servers` | Register a new server profile |
| GET | `/api/presets/export` | Export presets |
| POST | `/api/presets/import` | Import presets |

## GPU/RAM exhaustion behavior

Before loading, Console estimates weights, KV cache, GPU layers, configured
desktop reserve, and currently loaded engines. Use `/api/gpu/budget` and the
server `load-plan` endpoint to show this decision before calling `load`.

When a load cannot fit, the API returns a structured error containing:

```json
{
  "error": "insufficient_vram",
  "message": "...",
  "vram_free_gb": 4.5,
  "gpu_required_gb": 24.0,
  "unload_first": [{"server_id": "...", "label": "..."}],
  "retryable": false
}
```

Console does not kill unrelated applications automatically. An external app can
present `unload_first`, ask the user, unload a Console-owned engine, and retry.
If memory is temporarily busy or a model is still loading, use the readiness
response and retry after the supplied state transition rather than changing the
selected model.

## Error contract

Direct Console routes use FastAPI's standard envelope:

```json
{"detail": {"error": "model_not_loaded", "message": "...", "retryable": true}}
```

The gateway normalizes upstream failures into an OpenAI-shaped envelope:

```json
{
  "error": {
    "message": "Model loading is still in progress",
    "type": "unavailable_error",
    "code": 503,
    "reason": "model_loading",
    "retryable": true
  }
}
```

Typical status meanings:

| Status | Meaning |
|---|---|
| 400 | Invalid request or impossible setting |
| 404 | Unknown model, server, route, or job |
| 409 | Model mismatch, duplicate ownership, or lifecycle conflict |
| 422 | Validation error; fix the request fields |
| 429 | Temporary concurrency/rate limit; retry with backoff |
| 500/502 | Engine/provider failure; inspect `/api/console/logs?errors_only=true` |
| 503 | Not ready, loading, unavailable, or insufficient service capacity; retry only when `retryable=true` |

Never treat a model-not-loaded 400 as a readiness poll. JIT-capable chat routes
wait for the shared load operation; a genuine load failure is returned as a
retryable or terminal structured readiness error.
