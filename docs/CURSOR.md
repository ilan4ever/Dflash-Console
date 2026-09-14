# Connect Cursor to DFlash Console

Use this when **Cursor** (the IDE you code in) should run chat and agent turns against
models on **this PC** through DFlash Console — not against Cursor’s cloud models.

DFlash Console is **not** a separate “dflash serve --model …” one-shot server. It is the
full Console you install or run from git: UI on port **8900**, OpenAI gateway on **8001**.

---

## What is wrong in some online guides

| Claim | Reality |
|-------|---------|
| `dflash serve --model … --draft … --port 8000` | **`dflash serve`** only starts the Console (default UI port **8900**). There are no `--model` / `--draft` flags on `serve`. Load models in **Engines** or with `dflash load <name>`. |
| You must use ngrok / a public HTTPS URL | **Same PC:** use `http://127.0.0.1:8001/v1` — no tunnel. Tunnels are only if a tool on another machine or a cloud agent must reach your PC (not the usual Cursor desktop setup). |
| API on port **8000** | Default OpenAI gateway is **`gateway_port` = 8001** (`config.json` or **Settings → Engine profiles → Console OpenAI gateway**). |

---

## Prerequisites

1. **DFlash Console is running** — Windows app, `dflash serve`, or `.\run.ps1` from your checkout.
2. **Engines → Running** is **on** (standby blocks load/chat until the pipeline is armed).
3. A **chat model** is either already loaded on GPU or will **JIT-load** on the first gateway chat (normal for Cursor).

Check the gateway:

```powershell
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/v1/models
```

Use an **`id`** from `/v1/models` that is **loadable** (Console only lists models whose target file exists; broken DFlash stack ids may be omitted — use the hyphenated catalog id without `-dflash` if repair is needed).

---

## Cursor settings (local, recommended)

1. Open **Cursor Settings → Models** (or **Cursor Settings → API Keys** depending on your Cursor version).
2. **OpenAI API Key:** turn on and enter any non-empty placeholder (e.g. `dflash-local`). The local gateway ignores the key.
3. **Override OpenAI Base URL** (or “OpenAI Base URL”):  
   `http://127.0.0.1:8001/v1`  
   Include the **`/v1`** suffix.
4. **Add a custom model** for Cursor:
   - **Recommended:** `gpt-4o-mini` — Cursor’s UI accepts this name; the gateway maps it to your **default chat engine** (Settings → Engine profiles → Console OpenAI gateway).
   - **Or** use a catalog id **without** `-dflash`, e.g. `qwen3-8-27b-q6-k-l` from `GET /v1/models`.
   - **Do not** use `…-dflash` in Cursor if you see **Model name is not valid** — that message comes from **Cursor’s** name check, not from DFlash.
5. In chat, pick that model from the model list.

**If you see “Model name is not valid”:** Cursor rejected the name before calling the gateway. Switch to `gpt-4o-mini` or a shorter catalog id. Keep **OpenAI API Key** toggled **on** (any placeholder key).

**If chat fails after that:** Cursor **Agent** sends requests through Cursor’s cloud and cannot use `127.0.0.1`. Use your HTTPS tunnel instead, e.g. **`https://dflash-dev.onevoiceai.in/v1`** (same `onevoice-tunnel` as OneVoice; hostname must be added in **Cloudflare Zero Trust → tunnel ingress**, not only in local `config.yml`). Regular **Chat** on the same PC may still work with `http://127.0.0.1:8001/v1`.

**Streaming:** leave streaming enabled; the Console gateway supports `"stream": true`.

**Tab autocomplete** in the editor usually stays on Cursor’s cloud model. That is a Cursor limitation for most local OpenAI-base-URL setups, not a DFlash bug.

**Client identity:** Cursor does not expose a UI field for `X-DFlash-Client`. Requests may show as **Unknown API client** on **Engines** unless you use a proxy or extension that adds the header. Chat still works.

---

## Optional: default engine in Console

If Cursor sends a model name the gateway does not recognize, set **Settings → Engine profiles → Console OpenAI gateway → Default chat engine** to the profile you use most often. The gateway still prefers an explicit `model` field when it matches a catalog id.

---

## Verify from PowerShell

```powershell
curl -X POST http://127.0.0.1:8001/v1/chat/completions `
  -H "Content-Type: application/json" `
  -H "X-DFlash-Client: Cursor" `
  -d '{\"model\":\"qwen3-8-27b-q6-k-l\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi in one word\"}],\"stream\":false}'
```

If this works, point Cursor at the same base URL and model id.

---

## Remote access (advanced)

Only needed when **Cursor or another client is not on the same machine** as DFlash Console. Expose **8900/8001** only on a trusted network or through a tunnel you control, and add your own authentication in front of the gateway. The stock Console binds to **loopback** by design.

---

## More help

- **In app:** **Documentation → Console OpenAI gateway (port 8001)** and **Client identity**
- **Settings → API clients** — gateway URL and engine list
- [USER-GUIDE.md](./USER-GUIDE.md) §8 — calling engines
- [CLIENT-IDENTITY.md](./CLIENT-IDENTITY.md) — `X-DFlash-Client`
