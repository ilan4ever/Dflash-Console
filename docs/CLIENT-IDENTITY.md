# Client identity (`X-DFlash-Client`)

DFlash Console records **which app is using a loaded model**. This is shown on each
engine card (**Active client …**) and in API access logs. Identification is optional — loads
and inference are never blocked when the header is missing.

The banner updates whenever a client sends chat or embeddings to an already-loaded model,
not only on the first GPU load.

## Header

| Header | Value | Required |
|--------|-------|----------|
| `X-DFlash-Client` | Your app name (e.g. `OneVoice`, `My Research Bot`) | Recommended |

Use a short, stable name users will recognize on the Engines page.

## Behaviour

| Caller | Typical label |
|--------|----------------|
| DFlash Console UI | `DFlash Console` (sent automatically) |
| Your app with `X-DFlash-Client: YourApp` | `YourApp` |
| User-Agent / Referer containing `onevoice` | `OneVoice` (fallback) |
| Anything else without identification | `Unknown API client` |

The OpenAI gateway on port **8001** forwards `X-DFlash-Client`, `User-Agent`, and
`Referer` to the Console API — send the header on gateway requests too.

## When to send it

Send `X-DFlash-Client` on any request that can **load GPU weights**, including:

- `POST /api/servers/{id}/load`
- `POST /api/models/load`
- `POST /api/servers/{id}/v1/chat/completions` (JIT load on first chat)
- `POST /v1/chat/completions` on the gateway (`http://127.0.0.1:8001/v1`)
- `POST /api/runtimes/{id}/load`

## Examples

### curl (direct Console API)

```bash
curl -X POST http://127.0.0.1:8900/api/servers/gemma-12b-ar/load \
  -H "Content-Type: application/json" \
  -H "X-DFlash-Client: OneVoice" \
  -d "{}"
```

### curl (OpenAI gateway)

```bash
curl -X POST http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-DFlash-Client: OneVoice" \
  -d '{"model":"gemma-12b-ar","messages":[{"role":"user","content":"Hi"}]}'
```

### Python (httpx)

```python
headers = {
    "Content-Type": "application/json",
    "X-DFlash-Client": "OneVoice",
}
await client.post("http://127.0.0.1:8900/api/servers/gemma-12b-ar/load", headers=headers, json={})
```

### OpenAI Python SDK (gateway)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8001/v1",
    api_key="local",
    default_headers={"X-DFlash-Client": "OneVoice"},
)
client.chat.completions.create(model="gemma-12b-ar", messages=[{"role": "user", "content": "Hi"}])
```

### JavaScript (fetch)

```javascript
await fetch('http://127.0.0.1:8900/api/servers/gemma-12b-ar/load', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-DFlash-Client': 'MyApp',
  },
  body: '{}',
});
```

## Integrator checklist

1. Pick one display name per app (e.g. `OneVoice`, not `onevoice-worker-3`).
2. Add `X-DFlash-Client` to **every** load and chat path your app uses.
3. If you use the gateway (`:8001`), set the header on gateway requests (it is forwarded).
4. Reload the Engines tab after a request — the card banner should show your app name as **Active client**.
5. Missing header is allowed; the UI will show **Unknown API client**.

## Related

- Implementation: `core/client_identity.py`
- Gateway forwarding: `api/gateway.py` (`_FORWARD_HEADERS`)
- UI banner: Engines → loaded model cards → **Active client**
