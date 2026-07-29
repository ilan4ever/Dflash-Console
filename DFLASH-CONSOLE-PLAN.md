# DFlash Console — Full Build Plan

**Location:** `C:\dev\Dflash-Console` (UI/backend) · binaries/models in `C:\dev\Dflash`  
**Status:** **In progress — v1 Server + Models tabs working; handoff ready**  
**Last updated:** 2026-07-29  
**UI URL:** http://127.0.0.1:8900/

---

## 0. Handoff summary (read this first in a new chat)

### What works now

| Area | Done |
|------|------|
| **Backend** | FastAPI on **8900**, `config.json`, GPU scan, server start/stop/unload/reload |
| **Router mode** | llama-server starts with `--models-preset` + `--no-models-autoload`; load/unload via `/models/load` and `/models/unload` |
| **Legacy fallback** | If port has old **direct `-m`** process (LM Studio / old PS1 profile), **Eject** stops it and restarts **router idle** (server stays up, no model) |
| **Server tab** | LM Studio–style layout: status toggle, load settings (context, GPU layers, threads, batches), composite model card, **Eject**, developer logs |
| **Models tab** | Scans `~/.lmstudio/models` + DFlash models dir; **2 loadable profiles** pinned (from `config.json`); row **Load** button; inspector binding |
| **Status logic** | `probe_models` respects router `status.value` (loaded/unloaded/loading); no false LOADING after eject when router is idle |
| **Verified** | Browser: load Gemma 12B AR → Eject → **Running (idle)**, card gone, server still on 8092 |

### Configured servers (`config.json`)

| id | Label | Port | Profile | DFlash? | enabled |
|----|-------|------|---------|---------|---------|
| `gemma-31b-dflash` | Gemma 4 31B DFlash | 8090 | `gemma-chat` | yes (draft-dflash) | yes |
| `gemma-12b-ar` | Gemma 4 12B AR | 8092 | `gemma-12-ar` | no (AR only) | yes |
| `qwen-dflash` | Qwen3.5 27B DFlash | 8091 | `qwen-dflash` | yes | **disabled** |

**Loadable in UI** = rows with `server_id` / green **loadable** tag. Other GGUF files are **browse-only** until you add a server entry in `config.json`.

### How loading works (mechanism)

1. Each **server profile** in `config.json` → preset INI at `logs/presets/{server_id}.ini` (target GGUF + optional draft + load settings).
2. **Start / Load Model** → spawn router listener (if needed) → `POST /models/load` with `model_id`.
3. **Eject** → `POST /models/unload` (router) or legacy migrate → router idle.
4. **Non-DFlash GGUF** in the Models list is not wired unless you add a new server block + profile in PS1/config.

### Known issues / next chat priorities

1. **Dual-model load (8090 + 8092)** — User reports loading two models at once: GPU activity but UI looks stuck / unclear if both loaded. **Investigate:** parallel boots, status polling per-server, VRAM contention, boot progress for two servers, primary vs active server selection in UI.
2. **Gemma 31B DFlash load** — May fail on draft path case mismatch (`gemma-4-31B-it-DFlash-Q4_K_M.gguf` vs actual filename). Verify paths in preset / `start_llama_server.ps1` / model_stack.
3. **Legacy servers** — Anything started outside Console with `-m` breaks unload until migrated; Console now migrates on Eject and replaces legacy on Start, but LM Studio can still grab ports.
4. **Chat tab** — Not implemented (placeholder only).
5. **GPU sysbar** — Shows `— / GB`; no live VRAM polling yet.
6. **Qwen DFlash** — Disabled; enable in config when models/paths verified.
7. **Tests** — Minimal; expand runtime/router/eject tests.
8. **README** — Still says unload = stop server; update to match router eject behavior.

### Key files (recent work)

| File | Role |
|------|------|
| `core/runtime.py` | Status, `probe_models`, `router_unload_available`, unload/load HTTP |
| `core/server_boot.py` | Router spawn, `start_router_listener`, `eject_to_router_idle` |
| `core/model_presets.py` | INI preset generation |
| `core/load_progress.py` | Boot log markers (`boot`, `stop`, `model unload`, `router idle ready`) |
| `core/local_models.py` | Models catalog API |
| `static/js/server-live.js` | Server tab poll, eject/load, inspector |
| `static/js/models-live.js` | Models table, Load buttons |
| `api/app.py` | REST including `/api/models`, unload with legacy fallback |
| `C:\dev\Dflash\scripts\start_llama_server.ps1` | `-RouterMode -ModelsPreset` |

### Restart

```powershell
cd C:\dev\Dflash-Console
.\run.ps1
```

---


## 1. Executive summary

Build a **standalone local web app** (“DFlash Console”) that manages D-Flash llama-server profiles with an LM Studio–like control panel: start/stop, GPU assignment, live load status, unload, and OpenAI-compatible API URLs. Reuse proven code from **OneVoice** and **Dflash** rather than rebuilding CLI logic in Node.js.

---

## 2. Issues in the pasted agent prompt (fix before building)

| Issue | Pasted prompt says | Your reality |
|--------|-------------------|--------------|
| **Spec type flag** | `--spec-type dflash` | Correct value is **`--spec-type draft-dflash`** (also `draft-dspark` for Bonsai) |
| **Draft model flag** | `--spec-draft-model` | llama.cpp uses **`-md`** (long form may vary by build) |
| **Default port** | `8080` | Your profiles use **8090** (31B DFlash), **8092** (12B AR), **8091** (Qwen), **8082** (Bonsai) |
| **Tech stack** | Node.js + Express spawning raw CLI | You already have **`start_llama_server.ps1`** with tested profiles + OneVoice GPU/idle-unload wiring — **don’t rebuild CLI construction in Node** |
| **“Copy all files”** | Implied full repo copy | Would bloat the app with OneVoice chat/RAG/agents. **Copy/adapt ~5 modules**, not whole repos |
| **Single backend** | Only llama-server | Dflash has **two engines**: `llama-server.exe` (Gemma/Qwen/Bonsai) and **`test_dflash.exe`** (native Qwen DFlash, port 8000) |
| **Draft GPU flags** | `--spec-draft-device`, `--spec-draft-threads` | Your script uses **`-ngl`**, **`--main-gpu`**, **`--split-mode`**, **`--tensor-split`**; draft pinning may need **`-devd`** / **`--spec-draft-ngl`** (build-dependent) — expose gradually |
| **KV cache flags** | `--spec-draft-type-k/v` | Your Gemma profile uses **`--cache-type-k q4_0`** / **`--cache-type-v q4_0`** on the **target** side |

**Verdict:** The pasted prompt is a good *concept*, but wrong on flags, ports, and stack. Build on what you already proved in OneVoice + `start_llama_server.ps1`.

---

## 3. Recommended product name

**Primary recommendation: DFlash Console**

- Clear, LM Studio–familiar
- Matches folder `Dflash-ui` without awkward casing
- Window title: **DFlash Console** · subtitle: *Local speculative decoding server*

**Alternatives:** FlashNode, SpecServe (more generic).

---

## 4. Goal (v1)

A **standalone local web app** that:

1. Starts/stops **llama-server** profiles (Gemma 31B DFlash, Gemma 12B AR, Qwen DFlash, Bonsai)
2. Shows **which model is loaded on which GPU** (live status)
3. Lets you **unload**, change GPU, context, idle timeout
4. Exposes **OpenAI-compatible URLs** for OneVoice / LM Studio / Open WebUI
5. Optional: simple **test chat** tab (not a full OneVoice clone)

**Out of scope for v1:** agents, memory, RAG, multi-user auth, Electron desktop shell.

---

## 5. Architecture (recommended)

```
┌─────────────────────────────────────────────────────────────┐
│  DFlash Console UI (Browser)                                  │
│  Dashboard · Profiles · GPUs · Logs · Test Chat · Settings   │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST
┌──────────────────────────▼──────────────────────────────────┐
│  Dflash-ui Python backend (:8900)                            │
│  FastAPI · runtime · gpu_devices · server_boot               │
└──────────────────────────┬──────────────────────────────────┘
                           │ spawn
┌──────────────────────────▼──────────────────────────────────┐
│  C:\dev\Dflash\scripts\start_llama_server.ps1                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  llama-server.exe → OpenAI /v1/chat/completions              │
└─────────────────────────────────────────────────────────────┘
```

| Layer | Choice | Why |
|-------|--------|-----|
| **UI** | Static HTML + CSS + vanilla JS (or Vite later) | Matches OneVoice patterns; no React overhead for v1 |
| **Backend** | **Python FastAPI** (port **8900**) | Same as `Dflash/scripts/server.py`; reuses OneVoice runtime code |
| **Process manager** | **Call existing `start_llama_server.ps1`** | Single source of truth for flags/profiles |
| **Config** | `Dflash-ui/config.json` | Simpler than OneVoice `llm_model.model_roles` |
| **Dflash root** | Point to `C:\dev\Dflash` (env `DFLASH_ROOT`) | Binaries/models stay in parent repo |

**Not recommended for v1:** Node.js re-implementing llama-server args; Electron wrapper (add in v2 if wanted).

---

## 6. Copy strategy (what moves into `Dflash-ui`)

### Copy & adapt (small, focused)

| Source | Destination | Changes |
|--------|-------------|---------|
| `OneVoice/core/dflash_runtime.py` | `Dflash-ui/core/runtime.py` | Replace role-scan with `config.servers[]` |
| `OneVoice/core/gpu_devices.py` | `Dflash-ui/core/gpu_devices.py` | Trim OneVoice deps |
| `OneVoice/core/llm_client.py` → `_ensure_dflash_server` | `Dflash-ui/core/server_boot.py` | Rename env to `DFLASH_ROOT`; no OneVoice locks |
| `OneVoice/ui/chat_ui.html` (D-Flash blocks only) | `Dflash-ui/static/` | Extract ~400 lines: endpoint cards, live status, GPU dropdown |
| `Dflash/scripts/start_llama_server.ps1` | **Keep in parent** | Dflash-ui calls it via relative path |
| `Dflash/scripts/bench_openai_speed.py` | Optional link/button | “Run speed test” |

### Reference only (do not copy)

- Full `chat_ui.html` (73k lines)
- OneVoice `ui/server.py` (25k+ lines)
- Rasa, memory, agents, providers for Groq/Ollama/LM Studio

### Optional v2: native Qwen engine

| Source | Use |
|--------|-----|
| `Dflash/scripts/server.py` + `test_dflash.exe` | Second backend tab “Native Qwen DFlash” on port 8000 |

**v1 focus:** llama-server profiles only (what OneVoice already uses).

---

## 7. Proposed folder layout

```
C:\dev\Dflash\Dflash-ui\
├── DFLASH-CONSOLE-PLAN.md       # this file
├── config.json                 # servers, ports, model paths, defaults
├── run.ps1                     # start UI on :8900, optional auto-boot
├── requirements.txt            # fastapi, uvicorn, pydantic
├── README.md
├── core/
│   ├── runtime.py              # status, unload, reload, probe /models
│   ├── gpu_devices.py          # scan GPUs, resolve auto/split
│   └── server_boot.py          # spawn start_llama_server.ps1
├── api/
│   └── app.py                  # FastAPI routes
├── static/
│   ├── index.html              # main dashboard
│   ├── css/studio.css          # dark professional theme
│   └── js/
│       ├── dashboard.js        # server cards, start/stop/unload
│       ├── config.js           # load/save config
│       └── chat.js             # optional minimal test chat
└── tests/
    └── test_runtime.py
```

**Parent repo unchanged:** `llama.cpp`, `models/`, `scripts/start_llama_server.ps1` stay under `C:\dev\Dflash`.

---

## 8. UI design (simple, professional)

### Layout (single page, dark theme)

```
┌─────────────────────────────────────────────────────────────┐
│  DFlash Console                    [Refresh]  localhost:8900 │
├──────────────┬──────────────────────────────────────────────┤
│  Servers     │  Gemma 4 31B DFlash          ● Loaded        │
│  ─────────   │  Port 8090 · RTX 4090 · 65536 ctx            │
│  Dashboard   │  Target: gemma-4-31B…  Draft: …DFlash-Q4_K_M │
│  Profiles    │  [Stop] [Unload] [Open API docs]              │
│  GPUs        │  ─────────────────────────────────────────  │
│  Logs        │  Gemma 4 12B AR              ● Loaded        │
│  Test Chat   │  Port 8092 · GPU auto → RTX 4090             │
│  Settings    │  [Stop] [Unload]                             │
└──────────────┴──────────────────────────────────────────────┘
│  Log stream (llama-server stdout/stderr)                     │
└─────────────────────────────────────────────────────────────┘
```

### Per-server card (from OneVoice D-Flash View — already validated)

- **Title:** display name from config (not hardcoded “31B”)
- **Planned GPU:** Automatic → RTX 4090 / forced GPU name
- **Server URL:** `http://127.0.0.1:8090/v1` (copy button)
- **Live:** Loaded model id from `/models`
- **Actions:** Start · Stop · Unload · Edit settings

### Settings panel (Edit, not auto-closing View)

- Profile picker (gemma-chat, gemma-12-ar, qwen-dflash, …)
- Target + draft model paths (file pickers / text fields)
- Context size, idle unload (minutes), batch size
- GPU: Automatic | GPU 0 | GPU 1 | Layer split + tensor split
- DFlash-specific: `--spec-draft-n-max`, spec type (draft-dflash / draft-dspark / none)
- Cache types: `--cache-type-k`, `--cache-type-v`

### Logs tab

- Tail subprocess output (WebSocket or SSE)
- Filter: boot / error / metrics

### Test Chat tab (optional v1.1)

- Minimal streaming chat against selected server URL
- No tools, no memory — just prove the server works

---

## 9. Accurate parameter mapping (UI → `start_llama_server.ps1`)

### Already wired (reuse as-is)

| UI control | PS1 param / llama flag |
|------------|-------------------------|
| Profile | `-Profile` |
| Port | `-Port` |
| Context | `-ContextSize` → `-c` |
| Idle unload (minutes) | `-IdleUnloadSeconds` → `--sleep-idle-seconds` |
| Main GPU | `-MainGpu` → `--main-gpu` |
| Split mode | `-SplitMode` → `--split-mode` |
| Tensor split | `-TensorSplit` → `--tensor-split` |
| Host | `-HostAddress` → `--host` |

### Profile presets (read-only defaults, editable in Advanced)

| Profile | Port | Spec | Target | Draft |
|---------|------|------|--------|-------|
| gemma-chat | 8090 | draft-dflash | Gemma 31B GGUF | Gemma DFlash draft |
| gemma-12-ar | 8092 | none | Gemma 12B GGUF | — |
| qwen-dflash | 8091 | draft-dflash | Qwen 27B | Qwen DFlash GGUF |
| bonsai-spec | 8082 | draft-dspark | Bonsai 27B | dspark draft |

### Phase 2 advanced (extend PS1 or pass extra args)

- `--spec-draft-n-max` (slider 4–16)
- `--cache-type-k` / `--cache-type-v` (dropdown)
- `-ngl` / `--spec-draft-ngl` (separate target vs draft VRAM)
- `-devd` (pin draft to specific GPU — important for dual-GPU speed)
- `--fit off` (avoid ctx errors with speculative decoding)
- Temperature / top-p (API defaults, not server boot — document in “API usage” panel)

---

## 10. API surface (Dflash-ui backend)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | UI alive |
| GET | `/api/gpu-devices` | GPU list (from adapted `gpu_devices.py`) |
| GET | `/api/servers` | Config + live status merged |
| GET | `/api/servers/{id}/status` | Port open, loaded models, launch signature |
| POST | `/api/servers/{id}/start` | Boot via PS1 |
| POST | `/api/servers/{id}/stop` | Kill port + clear tracking |
| POST | `/api/servers/{id}/unload` | Router `/models/unload`; legacy `-m` → migrate to router idle |
| POST | `/api/servers/{id}/reload` | Stop; next start uses new settings |
| GET | `/api/models` | Local catalog + loadable server profiles |
| GET/PUT | `/api/config` | Persist `config.json` |
| GET | `/api/logs/{id}` | Stream or poll server log |
| GET | `/` | Serve static UI |

**Proxy (optional v1.1):** `POST /v1/chat/completions` → forward to active server (convenience only).

---

## 11. Default `config.json` (starter)

```json
{
  "ui_port": 8900,
  "dflash_root": "C:\\dev\\Dflash",
  "servers": [
    {
      "id": "gemma-31b-dflash",
      "label": "Gemma 4 31B DFlash",
      "profile": "gemma-chat",
      "port": 8090,
      "api_url": "http://127.0.0.1:8090/v1",
      "model_id": "gemma-4-31b-it-dflash",
      "gpu_device": "auto",
      "context_size": 65536,
      "idle_unload_minutes": 60,
      "enabled": true
    },
    {
      "id": "gemma-12b-ar",
      "label": "Gemma 4 12B AR",
      "profile": "gemma-12-ar",
      "port": 8092,
      "api_url": "http://127.0.0.1:8092/v1",
      "model_id": "gemma-4-12b-it-qat",
      "gpu_device": "auto",
      "context_size": 65536,
      "idle_unload_minutes": 60,
      "enabled": true
    }
  ]
}
```

---

## 12. Implementation phases

### Phase 0 — Scaffold ✅

- Folder structure, `requirements.txt`, `run.ps1`, health endpoint
- **Gate:** `http://127.0.0.1:8900` loads UI

### Phase 1 — Runtime + status ✅ (partial)

- Adapted `runtime.py`, `gpu_devices.py`, `server_boot.py`
- Router mode, load/unload, status polling, legacy eject fallback
- **Remaining:** reliable dual-server parallel load UX; Gemma 31B draft path fix

### Phase 2 — Server cards UI ✅ (LM Studio shell)

- Server tab: cards, eject, load settings, logs, status toggle
- **Remaining:** live VRAM in header; cancel-load during boot polish

### Phase 2b — Models tab ✅ (partial)

- Catalog, loadable profile rows, Load button, inspector
- **Remaining:** add-server-from-GGUF flow; enable Qwen profile

### Phase 3 — Profiles + model paths 🔄

- Profile editor in Server Settings modal (basic)
- **Remaining:** path validation, missing-file warnings, advanced PS1 passthrough

### Phase 4 — Logs + polish 🔄

- Log tail in Server tab ✅
- **Remaining:** tests, README accuracy, two-pass verify for 31B DFlash + dual load

### Phase 5 (optional) — Test chat + bench ⏳

- Chat tab placeholder only
- Bench button not wired

---

## 13. Relationship to OneVoice

| Concern | Approach |
|---------|----------|
| OneVoice keeps using D-Flash | Point `luce_dflash` URLs at same ports (8090/8092) — **no change required** |
| Duplicate logic | Dflash-ui owns **server management**; OneVoice can later call Dflash-ui APIs instead of spawning PS1 itself (future refactor, not v1) |
| OneVoice-stable | **Do not touch** unless explicitly requested |

---

## 14. Risks & decisions for approval

1. **UI port 8900** — OK? (Avoids 8890 OneVoice, 8090 llama-server)
2. **v1 scope:** llama-server profiles only, or include native `test_dflash.exe` tab?
3. **Model path discovery:** Keep LM Studio paths for Gemma, or add “scan folder” UI?
4. **Multi-server:** Allow two profiles running at once (8090 + 8092) — **recommended yes** (matches today)
5. **Electron later?** Web-only first is faster; wrap later if you want a desktop icon

---

## 15. Additions beyond the pasted prompt

- **Reuse OneVoice D-Flash View** (already built and verified) instead of designing from scratch
- **Profile-based launcher** instead of raw CLI builder
- **GPU auto + split** logic from `gpu_devices.py`
- **Idle unload** in minutes (LM Studio–like VRAM control)
- **Reload on GPU change** (don’t leave stale loads)
- **Speed bench integration** from existing Dflash scripts
- **Reference:** llama.cpp built-in WebUI at `:8080` — borrow layout ideas, not replace DFlash-specific controls
- **Reference:** Open WebUI pattern in `bonsai-27b/` for “connect external API” docs

---

## 16. Approval checklist

**Original plan — approved implicitly by build. Open items:**

- [x] **Name:** DFlash Console
- [x] **Stack:** Python FastAPI + static HTML/JS
- [x] **Location:** `C:\dev\Dflash-Console`
- [x] **UI port:** 8900
- [x] **v1 core:** Server tab start/stop/eject, Models catalog, config, logs
- [ ] **Dual-server load UX** — both 8090 + 8092 stable with clear status
- [ ] **Gemma 31B DFlash** — load verified end-to-end
- [ ] **Qwen DFlash** — enable + verify
- [ ] **Chat tab** — minimal test chat
- [ ] **Electron / native Qwen engine** — deferred

---

## 17. References

- llama.cpp speculative decoding: https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md
- DFlash PR (merged): https://github.com/ggml-org/llama.cpp/pull/22105
- Local scripts: `C:\dev\Dflash\scripts\start_llama_server.ps1`
- OneVoice integration: `C:\dev\OneVoice\core\dflash_runtime.py`, D-Flash provider UI in `chat_ui.html`
