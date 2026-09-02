# Settings — Speech & runtimes

Panel route: **Settings → Speech & runtimes** (`#/settings/rt-runtimes`)

This panel manages the non-llama runtimes — Piper TTS, Whisper STT, **vLLM**,
**Transformers**, and **FreeToken** — installed as on-demand bundles under
`runtimes/`. Heavy engines are not in the Windows installer. It is separate from
the llama-engine runtime panel (see [settings-runtime.md](./settings-runtime.md)),
which controls llama-server and DFlash 1 / DFlash 2 stacks. Device rules here
are **per-runtime**; llama stacks use the global hardware settings.

Use **Install vLLM** for fast NVIDIA Hugging Face loads. Use **Install
Transformers** as the CPU-friendly fallback. Use **Install FreeToken** for large
MoE models through WSL2. On the Models tab, SafeTensors LLMs show an engine
picker so you can choose vLLM, Transformers, or FreeToken before Load.

## What it shows

- **Installed runtimes** — one card per entry in `config.json` → `runtimes[]`.
  Each card shows:
  - **Device policy** — `auto` · `gpu` · `cpu` (per-runtime override)
  - **Default voice** (Piper) — the voice the Playground **Speak** tab selects
    by default
  - **Default STT model** (Whisper) — the model the Playground **Transcribe**
    tab selects by default
  - **Allow CPU fallback** — fall back to CPU when GPU memory is tight
  - **VRAM budget (MB)** — `0` = unlimited
  - **Manifest** — resolved bundle binary + `manifest.json` version from
    `GET /api/runtimes/manifests`
- **GPU contention** — which Console runtimes or external apps are holding VRAM
  right now (`GET /api/gpu/contention`). Warns *"Console runtimes hold VRAM —
  stop others before loading"* when the Console's own llama stacks are loaded.
- **Loading behavior** — two optional guards when VRAM is busy:
  - **Auto-stop other Console runtimes on load** (`runtime_stop_others_on_load`)
    — when a GPU-contention check recommends `stop-others`, the server
    automatically unloads the other running Console (llama) engines before
    loading the target. Never touches embedding engines or external apps.
  - **Warn when a runtime runs on CPU** (`cpu_slow_warn`) — the Playground
    Speak/Transcribe panels show a subtle "⚠ … may be slow" reminder when the
    active runtime's device policy is `cpu`.

## How saving works

The **Save runtimes** button writes `runtimes[]` back to `config.json` via
`PUT /api/config`, together with the two loading-behavior toggles. Each runtime
entry is normalized by `normalize_runtime()` (`core/config.py`), which
validates:

- `runtime_id` must be a real adapter (`piper`, `stt`, …) — `llama-server` is
  rejected in `runtimes[]`
- `host` must be loopback (`127.0.0.1`)
- `port` must not collide with servers or other runtimes (`reserved_ports()`)
- `device_policy` ∈ {`auto`, `gpu`, `cpu`}, `allow_cpu_fallback`,
  `vram_budget_mb` ≥ 0

> Note: `ConfigPatch` (the PUT body model) must declare every top-level config
> section it accepts — a missing field is silently dropped by pydantic, so the
> PUT would return success without persisting. `runtimes`,
> `runtime_stop_others_on_load`, and `cpu_slow_warn` are declared fields and
> covered by regression tests in `tests/test_config.py`.

## VRAM guard on llama loads

Before any llama-server load/start (manual load, engine start, or JIT chat
load), `core/memory_guardrails.assess_load()` estimates the GPU memory the
selected launch will need (weights on GPU + KV cache, honoring the hardware
strategy) and compares it to **current free VRAM** plus a 1 GB headroom. If it
does not fit it returns `level: block` and the API **refuses with HTTP 400**
and an actionable message — it never stops, restarts, or kills the engine. When
free VRAM is tight (`usage_ratio ≥ 0.85`) it returns `warn` and still allows
the load. The read-only preview is `GET /api/servers/{server_id}/load-plan`.
Covered by `tests/test_memory_guardrails.py`.

## Example config

```json
{
  "runtimes": [
    {
      "id": "tts-main",
      "runtime_id": "piper",
      "label": "Piper TTS",
      "port": 0,
      "host": "127.0.0.1",
      "device_policy": "cpu",
      "default_voice": "en_US-lessac-medium",
      "allow_cpu_fallback": true,
      "vram_budget_mb": 0
    },
    {
      "id": "stt-main",
      "runtime_id": "stt",
      "label": "Whisper STT",
      "port": 8910,
      "host": "127.0.0.1",
      "device_policy": "gpu",
      "default_model": "C:\\path\\to\\whisper\\model_q4_k.gguf",
      "allow_cpu_fallback": true,
      "vram_budget_mb": 0
    }
  ],
  "runtime_stop_others_on_load": false,
  "cpu_slow_warn": true
}
```

## Related

- Playground **Speak / Transcribe / Embed** modes (`static/js/speak-live.js`);
  Transcribe also records from the microphone (button next to the audio-file
  picker) — the recording is encoded to WAV in the browser and sent to the
  Console's `/v1/audio/transcriptions` proxy.
- Console proxies: `/v1/audio/speech`, `/v1/audio/transcriptions`,
  `/v1/embeddings`, `/embed/batch`
- Adapter source: `core/runtimes/` (`base.py`, `registry.py`, `piper.py`,
  `stt.py`, `contention.py`)
- Process identity: `runtimes/process-tokens.json`
