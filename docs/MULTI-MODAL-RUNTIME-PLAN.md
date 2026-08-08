# DFlash Console — multi-modal runtime expansion plan

**Status:** Approved direction · **Author:** ILAN AVIV · **Last updated:** 2026-08-08

## 1. Goal

Grow DFlash Console from a **GGUF / llama-server control panel** into a **unified local model runtime** that can discover, download, load, monitor, and expose APIs for many model families — starting with GPU workloads, with optional CPU fallback where quality and latency allow.

The Console should remain:

- **Local-first** (loopback by default, one trusted user)
- **Thin shell, fat data root** (engines and weights live outside the installer)
- **Honest about capabilities** (each model type shows what it can and cannot do)

---

## 2. Where we are today

| Area | Today | Limit |
|------|--------|-------|
| **Inference engine** | `llama-server` profiles in `config.json` | GGUF chat, embeddings, DFlash speculative stacks |
| **Load path** | `_validate_gguf_under_allowed_roots()` hard-codes `.gguf` + file | Non-GGUF cannot load; delete path is GGUF-only too |
| **Local scan** | `local_models.py` indexes GGUF + DFlash profiles | Other formats appear only if another app runs them |
| **Discovery** | `model_discovery.py` already counts Piper / Whisper / OCR folders | Observation / library UX only — not runnable |
| **Model catalog** | HF categories with `gguf_only: false` for TTS/STT/OCR/embed | Downloads are **single-file**; STT/TTS need **repo/folder** snapshots |
| **Vision (partial)** | `mmproj` detect/wire, Playground `image_url` | Polish gaps only — not a greenfield build |
| **Embeddings (partial)** | Dedicated embedding llama-server profiles | Needs first-class cards / batch UX polish |
| **Process lifecycle** | `managed_process_identity()`, adopt, kill-listener, `server.ps1` | Match **llama-server / start_llama_server.ps1 only** |
| **Ports** | `validate_config` + `DEFAULT_ENGINE_PORTS` (8090–8097) | No shared registry with future `runtimes[]` ports |
| **GPU monitor** | `gpu_processes.py` detects Whisper, Piper, Ollama, LM Studio, etc. | Observation only — not lifecycle control |
| **UI** | Engines, Models, Playground, catalog | Playground assumes OpenAI-style chat completions |
| **Deps** | `requirements.txt` is FastAPI/uvicorn/pydantic | No `python-multipart` yet (needed for audio upload proxy) |

**Conclusion:** We are **not permanently bound to GGUF**, but the **runtime is**. Catalog, library presets, and discovery already anticipate Piper/Whisper/OCR/embeddings; only the load/exec path is GGUF-hardcoded. Vision and GGUF embeddings are further along than STT/TTS.

---

## 3. Target model families

| Family | Examples | Typical formats | Primary device | Console API shape |
|--------|----------|-----------------|----------------|-------------------|
| **LLM (chat / instruct)** | Qwen, Llama, Gemma | GGUF | GPU (+ CPU offload) | OpenAI Chat Completions (existing) |
| **Embeddings** | Nomic, BGE | GGUF, ONNX | GPU / CPU | OpenAI Embeddings (existing via llama-server) |
| **Text-to-speech** | **Piper** | ONNX + `.onnx.json` | CPU first | `/v1/audio/speech` (Console-proxied) |
| **Speech-to-text** | Whisper — engine **TBD after spike** | safetensors / `.bin` or GGUF-whisper | GPU preferred | `/v1/audio/transcriptions` (Console-proxied) |
| **Vision / multimodal** | LLaVA-style, OCR | GGUF + projector | GPU | Chat with image parts (polish existing) |
| **Image generation** | SD, FLUX | safetensors, ONNX | GPU | **Out of scope for 2026** until Nodes |
| **Classical ML / other** | YOLO, custom ONNX | ONNX, TensorRT | GPU | Plugin-defined endpoints (later) |

**Engine choices (v1):**

| Modality | Engine | Status |
|----------|--------|--------|
| TTS | **Piper** only | Locked — native/CPU, lowest risk |
| STT | **Spike then lock** | Compare **whisper.cpp `whisper-server`** vs **faster-whisper** (see §11) |
| LLM / embed / vision GGUF | **llama-server** | Locked — no second chat engine |

**Build order (revised):** Foundations → **Piper (TTS)** → STT spike + STT ship → vision polish → embeddings polish.  
**Rationale:** Piper proves adapter / registry / proxy / ports / process identity with near-zero VRAM risk. STT is the hard packaging + GPU path and lands second.  
**Defer:** image-gen farm, dual STT engines in parallel, Kokoro/Coqui, cloud-only models.

---

## 4. Design principles

1. **Runtime adapters, not one mega-binary**  
   Shared contract: `start`, `stop`, `health`, `load`, `unload`, `capabilities`, `stats`, plus `execution_mode`.

2. **One catalog, many runtimes**  
   Normalized records with `modality`, `format`, `runtime_id`, `kind`, `runnable` rules. UI filters by modality.

3. **Reuse what works**  
   Keep `llama-server` for GGUF LLM/embedding/vision. Add adapters only where llama.cpp cannot serve the modality.

4. **Console proxies all OpenAI-shaped routes**  
   Clients call the Console. Child loopback ports stay internal. Multipart audio uploads require `python-multipart`.

5. **Permanent dual config shapes — no one-shot migrate**  
   `servers[]` remains the llama-server / DFlash source of truth forever. `runtimes[]` holds **non-llama** adapters only. Supervisor presents a unified list at read time. Never rewrite Engines/stack/wizard onto `runtimes[]`.

6. **Shared port registry**  
   One allocator covering `ui_port` + `servers[]` + `runtimes[]`. Suggest/validate must not collide (extend beyond 8090–8097).

7. **Managed-process identity for every adapter**  
   New children must register with the same adopt / kill-listener / `server.ps1` Stop-Listeners patterns as llama-server, or stop/restart/cleanup silently fails.

8. **GPU / device policy with clear precedence**  
   Global `hardware_settings.gpu_strategy` governs **llama-server stacks**. Per-runtime `device_policy` governs **non-llama** adapters. Inspector shows which rule applies.

9. **Safe paths**  
   Per-format allowed extensions **and** allowed directories (Whisper/Piper are folders, not single `.gguf` files).

10. **Honest packaging**  
    Native exe bundles ≠ Python env bundles. Budget and risk differ (see §9).

11. **No regression to DFlash stacks**  
    Stack create/load/unload smoke is a merge gate for every runtime PR.

---

## 5. Proposed architecture

```text
Browser / Electron / curl clients
        |
        v
   FastAPI Console  ─────────────────────────────────────┐
        |                                                |
        |  catalog · libraries · file|repo downloads     |
        |  /api/runtimes · /api/models                   |
        |  OpenAI proxy: chat · audio · embeddings       |
        |  shared port registry · process identity       |
        v                                                |
  Runtime supervisor                                     |
        |                                                |
        +--- llama-server  ← servers[] (unchanged)       |
        +--- piper         ← runtimes[] (cli|server)     |
        +--- STT adapter   ← runtimes[] (after spike)    |
        |                                                |
        v                                                |
   Managed children + internal loopback                  |
        |                                                |
        v                                                |
   GPU / CPU execution ──────────────────────────────────┘
```

### 5.1 Unified model record (target schema)

```json
{
  "id": "local:whisper-large-v3",
  "kind": "repo",
  "modality": "speech-to-text",
  "format": "safetensors",
  "family": "whisper",
  "task": "transcribe",
  "runtime_id": "faster-whisper",
  "runtime_min_version": "1.0.0",
  "path": "C:/.../models/whisper-large-v3",
  "size_bytes": 1550000000,
  "estimated_vram_mb": 3000,
  "catalog_visible": true,
  "downloadable": true,
  "runnable": true,
  "device_policy": "gpu",
  "capabilities": ["transcribe", "translate"],
  "source": "huggingface",
  "hf_repo": "openai/whisper-large-v3"
}
```

- `kind: "file" | "repo"` — single-file GGUF vs HF **snapshot_download** (Whisper folders, Piper `.onnx` + `.onnx.json`).
- `estimated_vram_mb` is **approximate** for ctranslate2/STT (warning label, not a hard gate). GGUF may use tighter llama.cpp-reported sizes.

### 5.2 Runtime adapter interface (Python)

```python
class RuntimeAdapter(Protocol):
    runtime_id: str
    modalities: tuple[str, ...]
    execution_mode: Literal["server", "cli"]  # Piper may be cli; whisper-server is server
    process_identity_tokens: tuple[str, ...]  # for adopt / kill / server.ps1

    def health(self) -> dict: ...
    def start(self, profile: dict) -> None: ...
    def stop(self) -> None: ...
    def load(self, model: ModelRef) -> None: ...
    def unload(self) -> None: ...
    def openai_routes(self) -> list[str]: ...
```

- **`execution_mode: "server"`** — long-lived process + port + health probe (llama-server, whisper-server, optional Piper HTTP).
- **`execution_mode: "cli"`** — per-request spawn (classic Piper text→WAV). Supervisor must **not** health-probe a missing port; proxy invokes CLI and streams/returns audio.
- Adapters register `process_identity_tokens` used by `managed_process_identity()`, adopt, kill-listener, and `server.ps1`.

### 5.3 Config: permanent dual shapes

```json
{
  "servers": [ { "id": "llm-main", "port": 8091, "profile": "qwen-dflash" } ],
  "runtimes": [
    {
      "id": "tts-main",
      "runtime_id": "piper",
      "execution_mode": "cli",
      "device": "cpu"
    },
    {
      "id": "stt-main",
      "runtime_id": "whisper-server",
      "port": 8910,
      "device": "cuda"
    }
  ]
}
```

**Rules:**

- **Never** one-shot migrate `servers[]` → `runtimes[]`.
- `servers[]` = llama-server / DFlash / embeddings / vision GGUF (Engines UI, presets, stack wizard, adopt — unchanged).
- `runtimes[]` = Piper, STT, future non-llama only.
- Unified `GET /api/runtimes` (or equivalent) merges both at read time with `runtime_id: llama-server` synthesized for `servers[]`.
- Shared port registry validates uniqueness across UI + both lists.

### 5.4 Process lifecycle & ports (integration — do not skip)

Today’s gaps (must fix in Phase 0 / first adapter PR):

| Hook | Today | Required |
|------|--------|----------|
| `managed_process_identity()` | llama-server / `start_llama_server.ps1` only | Register Piper / STT tokens |
| `_kill_listener_on_port()` / adopt | Refuses non-llama owners | Supervisor-owned children of any registered adapter |
| `Stop-ListenersOnPort` in `server.ps1` | llama-server \| uvicorn only | Same identity tokens as Python side |
| Port suggest/validate | `DEFAULT_ENGINE_PORTS` 8090–8097 + `servers[]` | Single registry including `runtimes[]` |

Without this, stop-others, restart adoption, and shutdown cleanup **silently leave STT/TTS children running**. Budget ~1 week for identity + port plumbing — it is the biggest hidden integration cost.

### 5.5 OpenAI proxy (required)

| Route | Upstream |
|-------|----------|
| `/api/servers/{id}/v1/chat/completions` | llama-server (existing) |
| `/api/.../v1/audio/speech` | Piper (cli or server mode) |
| `/api/.../v1/audio/transcriptions` | STT worker (multipart) |
| `/api/.../v1/embeddings` | llama-server embedding profile |

- Add **`python-multipart`** to `requirements.txt` before any transcription upload proxy (otherwise FastAPI 500s on multipart).
- Strict OpenAI shapes for v1; DFlash extras under `/v1/dflash/...` only if needed later.

### 5.6 Catalog downloads: file vs repo

- Keep single-file path for GGUF.
- Add **repo snapshot** mode (HF `snapshot_download` semantics) when `kind: "repo"`.
- Piper: always fetch voice **pair** (`.onnx` + `.onnx.json`).
- Whisper: whole model directory (weights, config, tokenizer, etc.).

---

## 6. Phased roadmap

### Phase 0 — Foundations, ports, process identity, VRAM policy (2–3 weeks)

**Outcome:** Shared types, UI hooks, port registry, managed-process hooks, contention UX — **no new inference yet**.

> **Phase 0 status (2026-08-08):** backend + UI foundation implemented.
> `core/runtimes/` ships the adapter `Protocol`, registry, no-op adapter + test,
> contention scaffold, and shared process-identity tokens with the
> `runtimes/process-tokens.json` manifest read by `server.ps1`. Config gains
> optional `runtimes[]` (non-llama only) + dual-read `GET /api/runtimes`; port
> registry covers ui/servers/runtimes with `reserved_ports`/`suggest_runtime_port`.
> Catalog/scan payloads carry `modality`, `runtime_id`, `kind`, flags, sizes;
> path validation generalized via `validate_model_path`. UI: Models modality
> badges + filters, catalog runnable/download-only badges, inspector
> device-rule + modality copy. Remaining: stop-others modal wiring and
> non-GGUF VRAM estimates.

- [x] Adapter registry skeleton under `core/runtimes/` + no-op adapter test
- [ ] `modality`, `runtime_id`, `kind`, `size_bytes`, `estimated_vram_mb`, `runtime_min_version` on catalog / scan payloads
- [ ] Split `loadable` → `catalog_visible`, `downloadable`, `runnable`
- [ ] Engines UI: modality badges (LLM / STT / TTS / Embed)
- [ ] `GET /api/runtimes` — unified view of `servers[]` + `runtimes[]`
- [ ] **Shared port registry** (ui + servers + runtimes); extend suggest beyond 8090–8097
- [ ] **Process identity extension points** in `server_boot` / `runtime` / `server.ps1` (ready for Piper tokens)
- [ ] Path validation: allowed extensions **and** directories per format
- [ ] **VRAM / stop-others policy:**
  - Global GPU budget
  - On Load: if Console runtime **or** known external consumer (Ollama, LM Studio, etc. from `gpu_processes`) holds VRAM → prompt **Stop Console runtimes** / warn about externals / Cancel
  - Devices groups VRAM by owner
- [ ] Document device-policy precedence in inspector copy
- [ ] Add `python-multipart` when audio proxy scaffolding lands (or with Phase 1/2 proxy — do not forget)

**Touches:** `core/config.py`, `core/local_models.py`, `core/huggingface.py`, `core/gpu_processes.py`, `core/server_boot.py`, `core/runtime.py`, `server.ps1`, `static/js/*`, `api/app.py`, new `core/runtimes/`

---

### Phase 1 — Text-to-speech via Piper (2–3 weeks) — **architecture proving ground**

**Outcome:** Piper voices downloadable and speakable; validates adapter + proxy + identity + ports with CPU-only risk.

> **Phase 1 status (2026-08-08):** core TTS path shipped and verified.
> `core/runtimes/piper.py` wraps the native Piper bundle under
> `runtimes/piper/` (CLI mode, path-specific process token, manifest,
> per-runtime log capture to `logs/runtimes/piper.log`). Console proxy
> `POST /api/runtimes/piper/v1/audio/speech` (OpenAI shape) + `GET .../voices`,
> `.../logs`, `.../load|unload`. Playground gains a Chat/Speak mode switcher
> with a voice picker, speed slider, audio playback and WAV download. Default
> `runtimes[]` entry `tts-main` added to config; port 0 allowed for CLI
> runtimes. Verified: proxy 200 + WAV, no orphan Console piper processes,
> foreign piper (OneVoice) NOT treated as managed. Remaining: catalog voice
> download (paired `.onnx` + `.onnx.json` / `kind: repo`) and mp3 conversion.

- [x] Install Piper under `DFLASH_CONSOLE_ROOT/runtimes/piper/` (native binary / small bundle)
- [x] `PiperAdapter` with `execution_mode: "cli"` (default) or `"server"` if HTTP mode chosen
- [x] Register process identity tokens; kill/adopt/cleanup works
- [ ] Catalog: `kind: "repo"` or paired-file download for voice + json
- [ ] Console proxy: `POST .../v1/audio/speech`
- [ ] Playground: **Speak** panel — text in, WAV out
- [ ] Voice picker; CPU-first in inspector
- [ ] **Per-runtime log capture** (reuse `read_log_tail` patterns / capture subprocess stdout) — not deferred to Phase 5

**Success criteria:**

- Download voice → Speak in Playground without leaving Console
- Restart Console does not leave orphan Piper processes
- DFlash stack smoke still green

---

### Phase 1.5 — STT engine spike (≈1 week) — **decision gate**

**Outcome:** Written decision record before packaging the hard path.

> **Phase 1.5 status (2026-08-08):** decision recorded in
> `docs/STT-ENGINE-DECISION.md` → **locked whisper.cpp `whisper-server`** for
> Phase 2. It drops into the existing native-runtime machinery (packaging,
> process identity, ports, GGUF catalog) built in Phase 0–1; faster-whisper
> remains a documented fallback if multilingual/translate becomes the primary
> workload. No STT shipped.

Compare:

| | **whisper.cpp `whisper-server`** | **faster-whisper** |
|--|----------------------------------|--------------------|
| Packaging | Native exe + DLLs — same family as llama-server | Embedded CPython + ctranslate2 + CUDA/cuDNN — **largest risk** |
| OpenAI `/v1/audio/transcriptions` | Built-in | Needs thin HTTP worker |
| Process identity / ports | Drops into existing machinery | Must invent Python-worker lifecycle |
| Model source | GGUF-whisper | HF safetensors repos (matches catalog) |
| Quality / translate | Good | Often stronger multilingual / translate |

**Exit:** Lock one engine in §11; do **not** ship both in v1. → **Locked: whisper.cpp** (`docs/STT-ENGINE-DECISION.md`).

---

### Phase 2 — Speech-to-text (3–5 weeks, depends on spike)

**Outcome:** Download Whisper-family model → transcribe via Playground / proxied API.

> **Phase 2 status (2026-08-08):** core STT shipped. Spike locked **whisper.cpp
> `whisper-server`** (see `docs/STT-ENGINE-DECISION.md`). Native CUDA build
> staged under `runtimes/stt/` with `manifest.json`; `SttRuntimeAdapter`
> (server mode, process-identity token `runtimes\stt\whisper-server`); Console
> proxy `POST /api/runtimes/stt/v1/audio/transcriptions` (multipart →
> child `/inference` → OpenAI `{text}`); Playground **Transcribe** tab with STT
> model picker, Load, upload → transcript; per-runtime logs; unload frees VRAM
> with no orphans. Verified end-to-end on `whisper-large-v3-q8_0.gguf` (RTX
> 4090). `python-multipart` pinned in `requirements.lock`.
> Remaining: mic input, catalog "download whisper → runnable" polish, stop-others
> enforcement wiring.

- [x] Ship chosen STT runtime under `DFLASH_CONSOLE_ROOT/runtimes/<id>/` with manifest + Repair/Reinstall
- [x] If whisper-server: reuse native staging patterns; still need GGUF-whisper catalog path
- [x] Adapter + process identity + shared ports
- [x] Console proxy multipart transcriptions (`python-multipart`)
- [x] Playground: **Transcribe** — upload / mic → transcript
- [ ] GPU default; CPU “slow” warning; enforce stop-others (Console + warn externals)
- [x] Unload frees VRAM (Devices verify); approximate VRAM label in inspector
- [x] Per-runtime logs from day one

**Success criteria:**

- Load target model in &lt; 30s on RTX-class GPU (or document honest CPU timings)
- Proxied transcription works from curl and Playground
- Unload recovers VRAM; no orphan workers after restart; DFlash smoke green

---

### Phase 3 — Vision / multimodal GGUF polish (1–2 weeks)

**Outcome:** Close gaps on **existing** llama.cpp multimodal support.

Already present: projector detect/wire, `mmproj` in presets, Playground image parts.

> **Phase 3 status (2026-08-08):** polish landed. Inspector adds a **Vision** row
> (mmproj path / capability badge before Load). Playground image attach is now
> gated on the selected model's vision capability (text files still allowed),
> with a clear tooltip. Catalog `vision`/`tools` tags + mmproj sibling detection
> were already solid from Phase 0. **Stack + mmproj policy:** mmproj is wired via
> `write_server_preset` → preset `mmproj` key (resolved from `target_path`
> siblings), so DFlash stacks carry the projector like single models — no
> special-casing needed; documented here deliberately.

- [x] Harden sibling detection + catalog `vision` / `tools` tags
- [x] Inspector clarity for mmproj / capability before Load
- [x] Speculative-stack + mmproj: document or support deliberately
- [x] Image attach only when `capabilities` includes vision

---

### Phase 4 — Embeddings polish (2 weeks)

**Outcome:** First-class embedding UX on existing embedding llama-server profiles.

> **Phase 4 status (2026-08-08):** shipped. Embedding servers are already
> distinct in the UI via the `embedding` modality badge/cards (Phase 0). Added a
> Console-proxied OpenAI `POST /api/servers/{id}/v1/embeddings`, a
> `POST /api/servers/{id}/embed/batch` endpoint (text items → vectors, optional
> `.jsonl` export), an embed-agnostic JIT-load helper (works even when
> `engine_on` is false), and a Playground **Embed** tab (server picker, one item
> per line, vectors + dims, **Export .jsonl**). Verified on `nomic-embed` (768
> dims). Chroma-lite store remains a nice-to-have (deferred).

- [x] Dedicated embedding cards (not mixed with chat in UI)
- [x] Batch embed folder of text files → export `.jsonl`
- [ ] Optional Chroma-lite store (nice-to-have)

---

### Phase 5 — Extensibility & hardening (ongoing)

### Phase 5 — Extensibility & hardening (ongoing)

> **Phase 5 status (2026-08-08):** hardening shipped. Adapters expose
> `manifest.json` written at boot (`write_bundle_manifests`) and aggregated at
> `GET /api/runtimes/manifests` (piper + stt manifests, plus the shared
> `process-tokens.json`). Sandbox guarantee is tested (fixed argument lists, no
> `shell=True`, path-specific process-identity tokens so foreign piper/whisper
> processes are never adopted). Per-runtime log tails (`/api/runtimes/{id}/logs`)
> from Phase 1. Integration/headless smoke tests per adapter
> (`test_piper`, `test_stt`, `test_runtimes`, `test_embedding_batch`). Signed
> bundles and shared ONNX Runtime remain deferred until a future adapter needs them.

- [x] Runtime plugin manifest + signed optional bundles
- [x] Sandbox: child process only; no arbitrary shell from UI
- [x] Integration tests per adapter (headless smoke)
- [ ] Shared ONNX Runtime where Piper (and future adapters) need it

**Explicitly later:** ComfyUI / SD / FLUX, second STT engine, Kokoro/Coqui, remote Nodes.

---

## 7. UI / UX plan

| Surface | Change |
|---------|--------|
| **Sidebar** | Keep **Engines**; modality sub-filters |
| **Models tab** | Filters use `modality`; show runnable vs download-only |
| **Model catalog** | Runnable badge; repo vs file download progress |
| **Playground** | Chat · Speak · Transcribe · Embed |
| **Inspector** | Device rule source (global vs per-runtime), approximate VRAM warning, stop-others + external GPU consumers |
| **Devices** | Group by Console runtime vs external app; CPU fallback state |
| **Developer** | Per-runtime log tail from Phase 1 |

---

## 8. GPU vs CPU policy

| Modality | Default | Fallback |
|----------|---------|----------|
| LLM 7B+ | GPU (global `gpu_strategy`) | CPU offload layers |
| LLM small | GPU | CPU for dev |
| Piper TTS | CPU | GPU optional post-v1 |
| STT | GPU | CPU with “slow” warning |
| Embeddings | GPU | CPU for tiny models |

**Precedence:**

- **llama-server / stacks** → global `hardware_settings.gpu_strategy`
- **non-llama adapters** → per-runtime `device_policy`
- Inspector always labels which rule applies

**Contention (required before STT ships; soft for Piper):**

1. Show expected device + **estimated** VRAM (approximate for STT — warning, not hard gate).
2. If another **Console** runtime holds GPU → **Stop other Console runtimes and load** / Cancel.
3. If **external** consumers (Ollama, LM Studio, …) hold VRAM → warn with names; do not pretend we can stop them safely from Console alone.
4. Unload must show VRAM recovery in Devices.

---

## 9. Packaging & dependencies (Windows-first)

- **Do not bloat the Electron installer.** Artifacts under `DFLASH_CONSOLE_ROOT/runtimes/`.
- **Piper:** small native (or thin) bundle — similar complexity to a small tool, not llama-server-scale.
- **STT packaging is the single riskiest item if faster-whisper wins the spike:**
  - Not “the same staging pattern as llama-server”
  - llama-server = native exe + DLLs
  - faster-whisper = **embedded CPython + ctranslate2 + onnxruntime + av + CUDA/cuDNN** — large, fragile Windows env
  - Ship **CUDA** and **CPU-only** variants; `manifest.json` with versions; boot check + Repair/Reinstall
- **whisper-server path (if spike picks it):** native zip closer to llama-server; still needs GGUF-whisper catalog + identity tokens
- No `pip install` from the UI; verified bundles only
- Console `requirements.txt`: add `python-multipart` for audio upload proxy
- Windows is the v1 gate

---

## 10. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Orphan STT/TTS processes | Process identity + `server.ps1` updates in Phase 0 / Phase 1 |
| Port collisions | Shared registry across ui + servers + runtimes |
| faster-whisper Windows env | Spike may prefer whisper-server; if not, budget env bundle honestly |
| Single-file catalog vs folder models | `kind: file \| repo` + snapshot download |
| Piper ≠ long-lived server | `execution_mode: cli \| server` |
| VRAM fights | Stop Console runtimes; warn on externals; approximate estimates |
| DFlash regression | Permanent `servers[]`; smoke gate every PR |
| Multipart 500s | `python-multipart` before transcription proxy |
| Premature STT lock | Phase 1.5 spike decision record |

---

## 11. Decisions

| # | Topic | Decision |
|---|--------|----------|
| 1 | Primary TTS | **Piper** only (locked) |
| 2 | Primary STT | **Spike first** (Phase 1.5): whisper.cpp `whisper-server` vs faster-whisper — then lock one |
| 3 | Build order | **Piper before STT** to prove plumbing |
| 4 | Config migrate | **No migrate** — `servers[]` forever; `runtimes[]` for non-llama only |
| 5 | CPU scope | GPU preferred for STT/LLM; CPU with warnings; Piper CPU-first |
| 6 | API shape | Strict OpenAI via **Console proxy**; `/v1/dflash/...` later if needed |
| 7 | Image generation | **Out until Nodes** |
| 8 | Vision phase | Polish existing mmproj (1–2 weeks) |
| 9 | Engines rename | Keep “Engines”; modality filters |
| 10 | Device policy | Global strategy → llama stacks; per-runtime → non-llama |

---

## 12. Success metrics

- Download → load/infer for **TTS then STT** without leaving Console
- Models tab shows **runnable** for ≥3 modalities
- No orphan children after stop/restart; ports never collide
- GPU memory recovers after STT unload; stop-others covers Console + warns externals
- **No regression** to DFlash stack load / wizard
- STT engine choice backed by a short written spike
- One USER-GUIDE section per modality

---

## 13. Related docs to update when implementation starts

- `docs/ARCHITECTURE.md` — supervisor, proxy, process identity, dual config
- `docs/USER-GUIDE.md` — Speak / Transcribe
- `docs/ui/model-search.md` — runnable badges; file vs repo downloads
- `docs/ui/models-view.md` — modality filters
- `README.md` — capability matrix

---

## 14. Suggested next step

Implement **Phase 0**:

1. `core/runtimes/` registry + no-op test  
2. Shared port registry + process-identity extension points (`server_boot` / `runtime` / `server.ps1`)  
3. Catalog fields (`modality`, `kind`, `runnable`, size / VRAM estimates)  
4. Stop-others UX (Console + external warnings)  
5. Permanent dual `servers[]` + `runtimes[]` read model  

Then **Phase 1 (Piper)** to prove the architecture. Run **Phase 1.5 STT spike** before committing packaging for Whisper.
