# Agent prompt — D-Flash TranslateGemma performance & readiness

Copy everything below the line into the **AI-Tools** Cursor agent.

---

## Context

DFlash Console **0.3.146+** loads TranslateGemma adhoc on `gemma-4-12b-it-q4-k-m-dflash`
correctly (`--no-jinja`, no draft/mmproj, `/load` waits for real readiness).

Investigation showed **slow translation and GPU “flashing”** were mostly caused by
**AI-Tools doing extra inference**, not by Console or the model being broken:

- `wait_for_translation_model_ready()` used to call `probe_dflash_translation_completions()`
  **every 2 seconds** — each probe was a **full chat completion** (`max_tokens=128`).
- `ensure_dflash_model_loaded()` / `prepare_dflash_translation_server()` ran that probe
  even when Console already reported `ready_for_chat: true`.
- Integration checklist step B used `engine/start` (idle router) instead of `POST /load`.

Direct Console API benchmark (model warm): **~1 s** for a short en→de translation.
Decode ~70 t/s is normal for 12B. Sysbar GPU % is sampled every 5s and looks “flashy”
during bursty LLM work — that is metering, not a fault.

Console fixed a **misleading UI badge** (“Draft required · repair” on adhoc TranslateGemma).
Your job is to finish/verify the **AI-Tools** side so translation is fast and does not
re-infer unnecessarily.

## Required headers (all D-Flash HTTP calls)

```
X-DFlash-Client: AI-Tools
X-DFlash-Strict-Model: 1
```

Use `dflash_request_headers()` / `dflash_chat_headers()` everywhere.

---

## Tasks

### 1. Trust Console readiness — stop probe loops

In `dflash_runtime.py`:

**Add or keep** `_translation_runtime_ready(server_id, model_id)`:

- `GET /api/servers` row for `server_id`
- Require `status != "error"`, `ready_for_chat is True`, and
  `active_model_id` matches requested model (`model_ids_match`).

**Change `wait_for_translation_model_ready()`**:

- Poll `_translation_runtime_ready()` every **2s** (status only — **no chat**).
- Return success when ready. **Do not** call `probe_dflash_translation_completions` in the loop.

**Change `ensure_dflash_model_loaded()` and `prepare_dflash_translation_server()`**:

- If `_translation_runtime_ready()` → return success **immediately** (no probe).
- Call `probe_dflash_translation_completions()` only when:
  - first-time setup after a failed load, or
  - explicit debug flag / one-shot validation — **not** on every translation job.

### 2. Shrink the translation probe

`probe_dflash_translation_completions()`:

- `max_tokens=32` (not 128).
- Short probe text only (`"Voilà, ça rend vichy."` → en).
- Keep `dflash_request_lock` on the chat URL.

### 3. Do not reload when already loaded

Before `POST /api/servers/{id}/load` with `model_path`:

```python
if _translation_runtime_ready(server_id, catalog_model_id):
    return True  # skip load entirely
```

After adhoc load succeeds, **do not** call `engine/start` or `/load` again for the
same checkpoint in the same session.

`prepare_dflash_translation_server()` should return `(server_id, active_model_id)`
from Console when ready — no second load.

### 4. Translation requests — right payload, no extra work

For D-Flash + TranslateGemma (`scraper.py` / `translate_to_language`):

- Use `build_translategemma_message_content()` structured content (not plain string).
- `stream: false` is fine.
- Use `request_max_tokens` from config for real work; **do not** default probes to 128.
- One chat POST per chunk — no duplicate probe before each chunk when server is ready.

### 5. Integration checklist

`scripts/dev/translategemma_integration_check.py`:

- **Step B**: `POST /api/servers/{id}/load` with empty body (loads default Gemma stack).
  Do **not** use `engine/start` alone.
- Steps D–G: existing strict headers and structured TranslateGemma payload.

### 6. Optional: timing log

Add debug timing when `AITOOLS_DFLASH_TIMING=1`:

```
prepare_dflash_translation_server: 0.05s (ready, skipped probe)
translate chunk 1/1: 1.2s (prompt=15, completion=17)
```

---

## Verify

1. Console **≥ 0.3.146** running at `http://127.0.0.1:8900`.
2. Run: `python scripts/dev/translategemma_integration_check.py`
3. **Pass A–H**.
4. Manual: one short translation from AI-Tools UI — should complete in **~1–3 s** when
   TranslateGemma already loaded (watch Engines: no repeated OUT 128 probe spam).
5. `GET /api/servers` during translate: `generating` may flicker; GPU sysbar % may jump
   0↔90 — that is normal sampling.

---

## Report back to DFlash Console agent

Reply with:

1. Console version from `/api/health`.
2. Checklist A–H pass/fail.
3. Before/after timing for `prepare_dflash_translation_server` + one short translation.
4. Confirm probe is **not** called in `wait_for_translation_model_ready` loop.
5. Files changed (expect `dflash_runtime.py`, maybe `scraper.py`, checklist script).

Subject: **AI-Tools D-Flash translation perf report**.
