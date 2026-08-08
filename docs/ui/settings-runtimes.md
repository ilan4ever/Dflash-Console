# Settings — Speech & runtimes

Panel route: **Settings → Speech & runtimes** (`#/settings/rt-runtimes`)

This panel manages the non-llama runtimes — Piper TTS and Whisper STT —
installed as native bundles under `runtimes/`. It is separate from the
llama-engine runtime panel (see [settings-runtime.md](./settings-runtime.md)),
which controls llama-server behavior. Device rules here are **per-runtime**;
llama stacks use the global hardware settings.

## What it shows

- **Installed runtimes** — one card per entry in `config.json` → `runtimes[]`.
  Each card shows:
  - **Device policy** — `auto` · `gpu` · `cpu` (per-runtime override)
  - **Allow CPU fallback** — fall back to CPU when GPU memory is tight
  - **VRAM budget (MB)** — `0` = unlimited
  - **Manifest** — resolved bundle binary + `manifest.json` version from
    `GET /api/runtimes/manifests`
- **GPU contention** — which Console runtimes or external apps are holding VRAM
  right now (`GET /api/gpu/contention`). Warns *"Console runtimes hold VRAM —
  stop others before loading"* when the Console's own llama stacks are loaded.

## How saving works

The **Save runtimes** button writes `runtimes[]` back to `config.json` via
`PUT /api/config`. Each entry is normalized by `normalize_runtime()`
(`core/config.py`), which validates:

- `runtime_id` must be a real adapter (`piper`, `stt`, …) — `llama-server` is
  rejected in `runtimes[]`
- `host` must be loopback (`127.0.0.1`)
- `port` must not collide with servers or other runtimes (`reserved_ports()`)
- `device_policy` ∈ {`auto`, `gpu`, `cpu`}, `allow_cpu_fallback`,
  `vram_budget_mb` ≥ 0

> Note: `ConfigPatch` (the PUT body model) must declare every top-level config
> section it accepts — a missing field is silently dropped by pydantic, so the
> PUT would return success without persisting. `runtimes` is a declared field
> and covered by regression tests in `tests/test_config.py`.

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
      "allow_cpu_fallback": true,
      "vram_budget_mb": 0
    }
  ]
}
```

## Related

- Playground **Speak / Transcribe / Embed** modes (`static/js/speak-live.js`)
- Console proxies: `/v1/audio/speech`, `/v1/audio/transcriptions`,
  `/v1/embeddings`, `/embed/batch`
- Adapter source: `core/runtimes/` (`base.py`, `registry.py`, `piper.py`,
  `stt.py`, `contention.py`)
- Process identity: `runtimes/process-tokens.json`
