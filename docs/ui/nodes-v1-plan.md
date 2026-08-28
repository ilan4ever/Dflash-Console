# Remote nodes — v1 plan

## Goal

Turn the **Nodes** tab from a placeholder into a working v1 that lets one DFlash Console
register other Console instances on the LAN (or VPN), check they are alive, and send a
test chat through them.

This is the minimum before lab-wide routing, encrypted tunnels, or pairing codes.

## v1 scope (shipping now)

| Area | v1 | Later |
|------|----|-------|
| Add/remove nodes manually | Yes | QR / pairing wizard |
| Node URL + optional API token | Yes | Rotating tokens, mTLS |
| Health ping (`/api/health`) | Yes | Engine-level drill-down |
| Online/offline badges in UI | Yes | GPU/model sync |
| Test chat via proxy | Yes | Route Playground/Engines picks |
| Persist in `config.json` | Yes | Trusted-device store |
| Encrypted transport | Tailscale + SSH wizard | mTLS / pairing codes |
| Load models on remote node | No | Remote engine picker |
| Workload scheduler | No | Queue + failover |

## Data model (`config.json`)

```json
"remote_nodes": [
  {
    "id": "lab-gpu-box",
    "label": "Lab GPU box",
    "base_url": "http://192.168.1.50:8900",
    "api_token": "",
    "enabled": true
  }
]
```

- `base_url` — root URL of the remote DFlash Console (no trailing slash).
- `api_token` — optional shared secret; sent as `Authorization: Bearer …` when set.
- Runtime health fields (`status`, `remote_version`, `last_checked_at`) are refreshed by
  the API and returned to the UI but are not required in config.

## API (v1)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/nodes` | List nodes (+ cached or fresh health) |
| POST | `/api/nodes` | Register a node |
| PATCH | `/api/nodes/{id}` | Update label, URL, token, enabled |
| DELETE | `/api/nodes/{id}` | Remove node |
| POST | `/api/nodes/{id}/health` | Force health check |
| POST | `/api/nodes/{id}/v1/chat/completions` | Proxy chat to remote `/v1/chat/completions` |

Health check: `GET {base_url}/api/health`.

Chat proxy: `POST {base_url}/v1/chat/completions` with the same JSON body (OpenAI shape).
Streaming responses are forwarded unchanged.

## UI (Nodes tab)

1. Header + **Connect securely** wizard (Tailscale or SSH tunnel) + **Add node** button.
2. Card list: label, URL, version, status badge, last checked.
3. Per-node actions: **Check**, **Test chat**, **Remove**.
4. Add-node modal: label, base URL, optional token.
5. Secure connect wizard: Tailscale (recommended) or SSH tunnel (advanced), test URL, add node.

## Remote machine requirements

Each node must:

1. Run DFlash Console with its API reachable from this PC (same LAN/VPN or port forward).
2. Have at least one engine loaded **or** gateway default engine configured if you expect
   chat tests to succeed immediately.
3. Optionally set a shared API token (future: env `DFLASH_CONSOLE_API_TOKEN` on both sides).

## v2 roadmap (not in this build)

1. **Pairing** — one-time code instead of pasting URLs/tokens.
2. **Remote catalog** — list engines/models from the node without SSH.
3. **Playground routing** — pick “Local” vs a registered node in the load bar.
4. **Encrypted tunnel** — WireGuard/Tailscale template or built-in relay.
5. **Scheduler** — send batch jobs to the least-busy node.

## Files touched in v1

- `core/config.py` — `normalize_remote_nodes`
- `core/remote_nodes.py` — CRUD, health, chat URL helpers
- `api/app.py` — REST + chat proxy route
- `static/js/nodes-live.js` — Nodes tab UI
- `static/index.html` — replace placeholder view + add modal
- `static/css/dflash-console.css` — node cards
- `tests/test_remote_nodes.py`
