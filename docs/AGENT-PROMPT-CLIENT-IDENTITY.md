# Agent prompt — add DFlash Console client identity

Copy everything below the line into your app’s Cursor/Copilot agent when that app
calls DFlash Console or the DFlash OpenAI gateway.

---

You integrate with **DFlash Console** (loopback API `http://127.0.0.1:8900` and/or
OpenAI gateway `http://127.0.0.1:8001/v1`). Add **client identification** so the
Engines UI shows which app loaded each model.

## Requirement

On **every HTTP request** that can load GPU models or trigger JIT load via chat,
send this header:

```
X-DFlash-Client: <APP_NAME>
```

Replace `<APP_NAME>` with a short, human-readable name for this project
(e.g. `OneVoice`, `MyLabAssistant`). Use the **same string everywhere** in this app.

## Apply to all of these paths (if your app uses them)

- `POST /api/servers/{engine_id}/load`
- `POST /api/models/load`
- `POST /api/servers/{engine_id}/v1/chat/completions`
- `POST /v1/chat/completions` on the gateway (`:8001`)
- `POST /api/runtimes/{runtime_id}/load`
- Any shared HTTP client wrapper — set `default_headers` once if possible

## Rules

- **Do not block** if the header is missing elsewhere; only **add** it in this app.
- **Do not rename** DFlash Console itself — only your app’s outbound calls.
- If using the **OpenAI Python SDK** against the gateway, use
  `default_headers={"X-DFlash-Client": "<APP_NAME>"}`.
- If using **fetch/axios/httpx**, merge the header into existing headers (do not
  drop `Content-Type` or `Authorization`).
- Gateway forwards `X-DFlash-Client` — sending it on `:8001` is enough for chat JIT loads.

## Verify

1. Run your app and trigger a model load or first chat.
2. Open DFlash Console → **Engines**.
3. The loaded model card should show **Active client &lt;APP_NAME&gt;** (updates on each chat/embed call).
4. If it shows **Unknown API client**, the header is missing on the load/chat request.

## Reference

Full examples: `docs/CLIENT-IDENTITY.md` in the DFlash Console repository.
