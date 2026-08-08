# Agent prompt — implement multi-modal runtime (DFlash Console)

Copy everything below the line into a coding-agent chat (or attach this file + `docs/MULTI-MODAL-RUNTIME-PLAN.md`).

---

## Mission

Implement the DFlash Console multi-modal runtime expansion **exactly** as specified in `docs/MULTI-MODAL-RUNTIME-PLAN.md`.

You are building adapters and plumbing so the Console can run **non-GGUF** modalities (starting with **Piper TTS**), while **llama-server / DFlash stacks stay untouched** as the GGUF path.

**Do not invent a parallel product.** Extend the existing FastAPI control plane, config, process lifecycle, catalog, and UI patterns.

## Source of truth

1. Read and follow: `docs/MULTI-MODAL-RUNTIME-PLAN.md` (full plan).
2. Cross-check behavior against existing code before changing it:
   - `core/config.py`, `core/local_models.py`, `core/model_discovery.py`
   - `core/huggingface.py`, `core/gpu_processes.py`
   - `core/server_boot.py`, `core/runtime.py`, `server.ps1`
   - `api/app.py`, `requirements.txt`
   - UI: `static/js/models-live.js`, `server-live.js`, `chat-live.js`, `model-search-live.js`
3. Follow workspace rules: version bump when packaging; restart Console after `api/**` / `core/**` / `config.json` / `requirements.txt` changes; browser-verify UI changes with a screenshot.

## Non-negotiable constraints

- **Local-first**, loopback children, one trusted user.
- **Thin installer / fat data root** — runtime binaries under `DFLASH_CONSOLE_ROOT/runtimes/`, not inside Electron.
- **No one-shot migrate** of `servers[]` → `runtimes[]`. Keep both forever:
  - `servers[]` = llama-server / DFlash / GGUF embeddings / vision (unchanged ownership).
  - `runtimes[]` = non-llama adapters only (Piper, later STT).
- **Console proxies** OpenAI-shaped routes; do not expose child ports as the public API.
- **No `pip install` from the UI**; verified runtime bundles only.
- **No image generation / ComfyUI / SD** in this workstream.
- **Do not regress** DFlash stack create / load / unload / wizard. Smoke those after every phase merge.
- Prefer small, reviewable PRs **one phase at a time**. Do not start Phase N+1 until Phase N acceptance criteria pass.

## Locked product decisions

| Topic | Decision |
|-------|----------|
| TTS | **Piper only** |
| STT | **Spike first** (whisper.cpp `whisper-server` vs faster-whisper); do not lock or ship both |
| Build order | Phase 0 → **Piper** → STT spike → STT → vision polish → embeddings polish |
| Device policy | Global `hardware_settings.gpu_strategy` → llama stacks; per-runtime `device_policy` → non-llama |
| Engines UI | Keep name “Engines”; add modality filters — no rename churn |

## Implementation order (do in this order)

### Phase 0 — Foundations (no new inference)

**Goal:** Registry, unified listing, port safety, process-identity hooks, catalog fields, stop-others UX scaffolding.

Deliver:

1. `core/runtimes/` package:
   - Adapter `Protocol` with: `runtime_id`, `modalities`, `execution_mode` (`server` | `cli`), `process_identity_tokens`, `health/start/stop/load/unload`, `openai_routes`
   - Registry + **no-op adapter** + unit test
2. Config:
   - Optional `runtimes[]` array (non-llama only)
   - Dual-read: `GET /api/runtimes` (or agreed name) merges `servers[]` (synthesized `runtime_id: llama-server`) + `runtimes[]`
   - **Never** rewrite existing Engines/stack APIs onto `runtimes[]`
3. **Shared port registry** covering `ui_port` + all `servers[]` ports + all `runtimes[]` ports; extend suggest/validate beyond `8090–8097`
4. **Process identity extension points** in `managed_process_identity()`, adopt, kill-listener, and `server.ps1` `Stop-ListenersOnPort` so new tokens can be registered (implement tokens fully when Piper lands)
5. Catalog / scan payloads: `modality`, `runtime_id`, `kind` (`file` | `repo`), `catalog_visible`, `downloadable`, `runnable`, `size_bytes`, `estimated_vram_mb`, `runtime_min_version`, `family`/`task` where easy
6. Path validation helpers: allowed extensions **and** allowed directories (prepare for Piper/Whisper folders; keep GGUF validation working)
7. VRAM / stop-others scaffolding:
   - Reuse `gpu_processes.py`
   - On load intent: if another **Console** runtime holds VRAM → prompt stop-others
   - If **external** (Ollama, LM Studio, …) → **warn by name**, do not claim Console can kill them safely
8. UI: modality badges / filters on Models; runnable vs download-only clarity where fields exist
9. Inspector copy: show whether device rule is **global** (llama) or **per-runtime** (non-llama)

**Acceptance:**

- [ ] No-op adapter test passes
- [ ] Unified runtimes list API works; existing Engines/`servers[]` behavior unchanged
- [ ] Port collision across ui/servers/runtimes rejected
- [ ] DFlash stack smoke still green
- [ ] Docs note in plan checklist updated or PR description maps to Phase 0 bullets

**Out of scope for Phase 0:** Actually running Piper/Whisper inference.

---

### Phase 1 — Piper TTS (prove the architecture)

**Goal:** Download voice → speak via Playground / Console proxy. CPU-first. This phase **must** prove process identity, ports, proxy, and logs.

Deliver:

1. Piper install under `DFLASH_CONSOLE_ROOT/runtimes/piper/` (native/small bundle + manifest)
2. `PiperAdapter` with `execution_mode: "cli"` by default (or `server` if you choose HTTP mode — declare it; do not health-probe a CLI)
3. Register Piper `process_identity_tokens` in Python **and** `server.ps1`
4. Catalog download: voice **pair** (`.onnx` + `.onnx.json`) via `kind: "repo"` or paired-file download — extend HF download beyond single-file where needed
5. Console proxy: OpenAI-ish `POST .../v1/audio/speech` (text → audio)
6. Playground **Speak** panel
7. **Per-runtime log capture** from day one (do not wait for Phase 5)
8. Add `python-multipart` only if this phase needs multipart; otherwise add it no later than STT proxy

**Acceptance:**

- [ ] Speak works end-to-end in UI
- [ ] Restart/stop leaves **no orphan Piper processes**
- [ ] Ports do not collide with llama engines
- [ ] DFlash stack smoke green

---

### Phase 1.5 — STT spike (decision only)

**Goal:** Written decision record — **do not ship STT product yet**.

Compare whisper.cpp `whisper-server` vs faster-whisper on:

- Windows packaging cost (native zip vs embedded Python+CUDA env)
- OpenAI `/v1/audio/transcriptions` fit
- Process identity / adopt / cleanup fit
- Catalog model source (GGUF-whisper vs HF safetensors repo)
- Quality / translate needs

**Deliverable:** short markdown decision under `docs/` (or PR comment) locking **one** engine. Do not implement both.

---

### Phase 2 — STT (after spike lock)

**Goal:** Download → load → transcribe via Console proxy + Playground Transcribe tab.

Must include:

- Chosen runtime under `runtimes/<id>/` with manifest + Repair/Reinstall
- If faster-whisper: honest **Python env** CUDA + CPU bundles (not “same as llama-server zip”)
- If whisper-server: native staging closer to llama-server; GGUF-whisper catalog path
- Repo snapshot downloads for model dirs
- Multipart proxy (`python-multipart` in `requirements.txt`)
- Process identity + shared ports + stop-others (Console stop + external warn)
- Approximate VRAM label (warning, not hard gate for ctranslate2)
- Logs from day one; unload frees VRAM (verify in Devices)

**Acceptance:** curl + Playground transcription; no orphans; VRAM recovers; DFlash smoke green.

---

### Phase 3 — Vision polish (existing mmproj path only)

Harden projector detection, inspector clarity, speculative-stack + mmproj policy (document or support deliberately), Playground image attach only when capability present. **Do not** build a new vision sidecar.

### Phase 4 — Embeddings polish

First-class embedding cards, batch folder embed, `.jsonl` export. Stay on existing embedding llama-server profiles.

### Phase 5 — Hardening (ongoing)

Plugin manifests, signed bundles, broader integration tests. Logs for Piper/STT are **already** required earlier.

## Coding standards for this repo

- Match existing style; minimal diffs; no drive-by refactors.
- Prefer extending existing modules over new frameworks.
- Keep security posture: path allowlists, loopback, no arbitrary shell from UI.
- After backend changes: run `.\scripts\restart-console-server.ps1` and confirm API up.
- After UI changes: open `http://127.0.0.1:8900/`, wait for auto-refresh, **screenshot** to verify.
- Do not commit unless the user asks.
- Do not bump version unless shipping installer/user-facing packaging for that change set (follow version-bump rule).

## How to work each session

1. State which **phase** and which **checklist bullets** you will complete.
2. Read the relevant existing code first; list files you will touch.
3. Implement the smallest vertical slice that meets acceptance.
4. Run tests / smoke DFlash stack load if you touched lifecycle.
5. Restart server if backend changed; screenshot if UI changed.
6. End with a short **Summary** of what changed and what remains for the phase.

## Explicitly do not do

- Rename Engines → Runtimes globally
- Migrate `servers[]` into `runtimes[]`
- Lock STT to faster-whisper without the spike (or ship whisper.cpp + faster-whisper together)
- Put CUDA/Python envs inside the Electron installer
- Expose raw child ports as the app API
- Start image-gen / Nodes / ComfyUI work under this plan
- Skip process identity / `server.ps1` updates when adding a child process
- Defer Piper/STT logging to “later hardening”

## First command for the agent

Start **Phase 0 only**. Create `core/runtimes/` with the adapter protocol, registry, no-op adapter + test, dual-read `GET /api/runtimes`, and shared port registry stubs. Do not implement Piper inference yet.
