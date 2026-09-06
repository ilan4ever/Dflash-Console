# Agent prompt — TranslateGemma adhoc load (AI-Tools integration)

Copy everything below the line into the **AI-Tools** Cursor agent. After you
finish testing, **reply in chat to the DFlash Console agent** with your report
(see “Report back” at the end).

---

## Context

DFlash Console fixed adhoc loading of **TranslateGemma** onto a DFlash engine slot
(e.g. `gemma-4-12b-it-q4-k-m-dflash`). Previously `/load` returned success while
the child llama-server crashed (wrong draft, mmproj, and Jinja template). Chat
also silently rewrote the wrong `model` name to whatever was loaded.

**Console repo:** `C:\dev\Dflash-Console` (restart the dev server if it was
already running before pulling these changes).

## What changed (Console side)

1. **Adhoc `/load`** always infers profile from the **target GGUF path**
   (`translategemma` → plain LLM: no DFlash draft, no mmproj, `jinja = false`).
2. **`POST /api/servers/{id}/load`** waits until the child checkpoint is
   **actually loaded** (or returns failure with log detail).
3. **Strict chat model check** — send header `X-DFlash-Strict-Model: 1` on chat
   requests. If `model` does not match the loaded checkpoint, Console returns
   **409** `model_mismatch` instead of rewriting the model name.

## Required AI-Tools changes

### 1. Headers on every Console call

```
X-DFlash-Client: AI-Tools
X-DFlash-Strict-Model: 1
```

Set both on:

- `POST /api/servers/{engine_id}/load`
- `POST /api/servers/{engine_id}/v1/chat/completions`
- `POST /v1/chat/completions` on the OpenAI gateway (`:8001`) if used

### 2. Load Gemma baseline (step B)

`POST /api/servers/gemma-4-12b-it-q4-k-m-dflash/load` with **no body** loads the
engine’s default DFlash stack. Do **not** use `/engine/start` alone — that only
starts an idle router with no checkpoint.

**Expect:** `active_model_id` contains `gemma-4-12b-it-q4-k-m`, `ready_for_chat: true`.

### 3. Load TranslateGemma (adhoc on DFlash engine)

Use the **DFlash engine id** (not the plain AR alias) when that slot is already
running:

```http
POST http://127.0.0.1:8900/api/servers/gemma-4-12b-it-q4-k-m-dflash/load
Content-Type: application/json
X-DFlash-Client: AI-Tools

{
  "model_path": "C:\\dev\\Dflash-Console\\models\\google\\translategemma-12b-it.Q4_K_S\\translategemma-12b-it.Q4_K_S.gguf",
  "model_id": "translategemma-12b-it.q4-k-s"
}
```

**Expect:**

- HTTP **200** only when load truly succeeded.
- Body includes `"loaded": true`, `"adhoc": true`, `"model": "translategemma-12b-it.q4-k-s"`.
- `GET /api/servers` → that engine: `status: "loaded"`,
  `active_model_id: "translategemma-12b-it.q4-k-s"`, `ready_for_chat: true`.
- If load fails, HTTP **409/400** with error text (no false success).

### 4. Translation chat payload

TranslateGemma expects **structured** user content (not a plain string). Example:

```json
{
  "model": "translategemma-12b-it",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "source_lang_code": "en",
          "target_lang_code": "de",
          "text": "Hello, how are you?"
        }
      ]
    }
  ]
}
```

Use the loaded id or a compatible alias (`translategemma-12b-it` ↔
`translategemma-12b-it-q4-k-s`).

### 5. Strict model mismatch test

While **Gemma** is loaded (not TranslateGemma), send chat with
`model: "translategemma-12b-it"` and `X-DFlash-Strict-Model: 1`.

**Expect:** HTTP **409**, `code: "model_mismatch"` (not a silent Gemma reply).

### 6. Reload Gemma after translation tests

Unload or load the default Gemma stack when finished so the engine card matches
user expectations.

## Test checklist

| Step | Action | Pass criteria |
|------|--------|---------------|
| A | `GET /api/health` | Console up |
| B | Load Gemma on `gemma-4-12b-it-q4-k-m-dflash` | `active_model_id` = gemma id |
| C | Chat with strict header, wrong model | 409 `model_mismatch` |
| D | Adhoc load TranslateGemma (body above) | 200, `loaded: true`, servers shows loaded |
| E | `GET /api/servers` | No `error` status; correct `active_model_id` |
| F | Translation chat with structured content | Valid translation response |
| G | Strict chat with `model: translategemma-12b-it` | 200 (compatible alias) |
| H | Load failure path (optional: bad path) | Error response, not `loaded: true` |

## Report back

**Reply to the DFlash Console agent** (same Cursor project or paste into the
DFlash Console chat) with:

1. Console version from `GET /api/health` (`version` field).
2. Pass/fail for steps A–H.
3. For any failure: HTTP status, response body, and last 30 lines of
   `logs/gemma-4-12b-it-q4-k-m-dflash.log`.
4. Confirm whether AI-Tools now sends `X-DFlash-Strict-Model: 1` on load + chat.
5. One sample translation output (source → target languages).

Subject line suggestion: **AI-Tools TranslateGemma integration report**.
