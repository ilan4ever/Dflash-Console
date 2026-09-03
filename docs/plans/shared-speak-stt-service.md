# Shared speak / STT service — future plan

**Status:** Planned (not in scope for current release)  
**Last updated:** 2026-03-03

## Problem

When multiple apps (OneVoice, DFlash Console Speak panel, other clients) need the
same speech-to-text model, each app may start its own GPU worker. That wastes
VRAM and shows up in **Engines** as several external cards for what is really one
logical model.

Example symptoms:

- Multiple **OneVoice** entries under **External** in Engines
- Several `speak_stt` / `faster-whisper` processes on the same machine
- Same whisper weights loaded more than once on the GPU

## Current architecture (today)

### DFlash Console

- **Own speech stack:** singleton runtime adapters (`stt`, `faster-whisper`,
  `piper`) — one Console-managed server process per runtime type.
- **External apps:** Console **observes** GPU processes via `nvidia-smi` and
  classifies them (`get_external_gpu_loads()`). It does **not** load OneVoice
  models.
- **OneVoice probe:** Console can read live STT status from OneVoice's
  `speak_stt` WebSocket on port **2711** (`_probe_onevoice_stt_status`).

### OneVoice

- Runs `speak_stt` as a **server** (WebSocket on `:2711`).
- Designed to be client/server, but each app instance may still bootstrap its
  own worker if nothing enforces a singleton at startup.

### Gap

Console and OneVoice do **not** coordinate model loading. Duplication is an
app-lifecycle issue, not a Console engine-loader issue.

## Target architecture (future)

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ OneVoice UI │   │ DFlash Speak│   │ Other app   │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
              ┌──────────────────────┐
              │  ONE speak_stt       │
              │  (loopback :2711)    │
              │  ONE STT model       │
              │  in VRAM             │
              └──────────────────────┘
```

**Principles:**

1. **One model, one GPU process** for a given STT checkpoint.
2. **Many clients** connect over loopback HTTP/WebSocket.
3. **Explicit handoff** when switching models (unload old → load new).
4. **Console displays** one logical card per shared service, not per client PID.

## Proposed work (phased)

### Phase 1 — OneVoice singleton guard (OneVoice repo)

- On startup, check whether `speak_stt` is already listening on `:2711`.
- If yes and the requested model matches → **attach** as client; do not spawn.
- If yes and model differs → negotiate switch (prompt or auto-unload policy).
- If no → start `speak_stt` once and register it as the system STT daemon.
- Ensure app/electron helper processes are not counted as separate model loads.

### Phase 2 — DFlash Console integration (this repo)

- When OneVoice STT is healthy on `:2711`, prefer **proxying** to it instead of
  starting Console's own `faster-whisper` worker for the same model path.
- Add runtime setting: `stt_provider: console | onevoice | auto` (default `auto`).
- Surface a single **Shared STT** card in Engines when proxying externally.

### Phase 3 — Engines UI deduplication (this repo)

- Group external cards by `(app_source, model_id, listen_port)`.
- Show child PIDs as metadata, not as separate "loaded model" rows.
- Keep per-PID **Unload** only for the actual model-holding process.

### Phase 4 — Cross-app policy (optional)

- Shared manifest or lockfile for "which STT model is canonical on this PC".
- Console contention UX (`core/runtimes/contention.py`) warns before loading a
  second STT runtime when one is already active.

## Out of scope for this plan

- Changing OneVoice's internal STT engine choice (stays `faster-whisper` /
  `speak_stt` unless decided separately).
- LAN-wide shared STT (loopback only; see `SECURITY.md`).
- Merging OneVoice and Console into one process.

## Success criteria

- Opening multiple OneVoice windows does not multiply VRAM for the same STT model.
- DFlash Console Speak/Transcribe reuses an existing healthy `speak_stt` when
  configured for `auto`.
- Engines shows **one** STT entry per active model, with clear "loaded by" info.
- Unload from Console or OneVoice stops the shared worker only when no clients
  remain (reference counting or explicit "stop service").

## References

- `core/gpu_processes.py` — external GPU scan, OneVoice STT probe
- `core/runtimes/stt.py`, `core/runtimes/faster_whisper.py` — Console STT adapters
- `core/runtimes/contention.py` — GPU contention scaffolding
- `docs/STT-ENGINE-DECISION.md` — whisper.cpp STT choice for Console bundle
- `docs/GOING-PUBLIC.md` — repository visibility checklist
