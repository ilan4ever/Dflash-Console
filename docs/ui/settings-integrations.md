# Settings — MCP & clients

The current integrations panel is named **API clients** (settings id `int-mcp`). It shows the local
Console endpoint and generated client information for connecting compatible
tools to the running service.

## Supported information

- Loopback Console base URL
- OpenAI-compatible engine URLs
- Console proxy route for chat completions
- **Client identity** — integrators should send `X-DFlash-Client: YourApp` on load and chat
  requests; shown on Engines as **Loaded by …** (see Documentation → Client identity)
- MCP/client configuration preview when available

The panel does not provide account login, webhooks, arbitrary outbound headers,
or remote-node management. Client identity is the one optional outbound header
documented for integrators today.
