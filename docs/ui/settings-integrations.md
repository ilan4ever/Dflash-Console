# Settings — MCP & clients

The current integrations panel is named **API clients** (settings id `int-mcp`). It shows the local
Console endpoint and generated client information for connecting compatible
tools to the running service.

## Supported information

- Loopback Console base URL
- OpenAI-compatible engine URLs (**gateway** default `http://127.0.0.1:8001/v1`, not port 8000)
- Console proxy route for chat completions
- **Client identity** — integrators should send `X-DFlash-Client: YourApp` on load and chat
  requests; shown on Engines as **Loaded by …** (see Documentation → Client identity)
- **Cursor IDE** — see Documentation → **Connect Cursor IDE** or `docs/CURSOR.md`
- MCP/client configuration preview when available

## Common mistakes (Cursor and other clients)

| Wrong | Correct |
|-------|---------|
| `dflash serve --model X --draft Y --port 8000` | `dflash serve` or the desktop app; load models in **Engines** or `dflash load` |
| ngrok required on one PC | Use `http://127.0.0.1:8001/v1` locally |
| Model name = engine profile id only | Use an `id` from `GET http://127.0.0.1:8001/v1/models` |

The panel does not provide account login, webhooks, arbitrary outbound headers,
or remote-node management. Client identity is the one optional outbound header
documented for integrators today.
