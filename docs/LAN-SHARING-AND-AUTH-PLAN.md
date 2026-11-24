# Plan: sign-in, pairing, and sharing on the local network

**Status:** proposal, not started (2026-11-24). Needs the maintainer's approval before work begins: it changes the security model in [ARCHITECTURE.md](./ARCHITECTURE.md#security-model).

## Why

Today every DFlash Console listens only on `127.0.0.1`. Each machine's Console has the same address (`http://127.0.0.1:8900`), and nothing else can reach it. That is safe, but:

- To use another PC's GPU ([SHARE-A-GPU.md](./SHARE-A-GPU.md)) on a home network, users must change how the Console starts or set up Tailscale or an SSH tunnel. Most users won't sign up for another service.
- There is no sign-in. A Console that other machines can reach could be used by anyone on that network: load models, use the GPU, browse folders (`/api/fs/browse`), start downloads, or stop the Console (`/api/shutdown`).

The goal: one switch that shares a Console on the local network without any outside service, and a sign-in that makes this safe.

## What exists already

| Piece | Where | State |
|---|---|---|
| Remote nodes (add a node by URL, share its GPU) | `core/remote_nodes.py`, Nodes tab | Works over a reachable URL |
| Client sends `Authorization: Bearer <api_token>` to a node | `core/remote_nodes.py` `_node_headers` | Sent, but **no Console checks it** |
| "Connect securely" wizard (Tailscale / SSH recipes) | `core/node_connect.py` | Instructions only |
| Bind address | `dflash_cli/commands.py`, `server.ps1`, `server.sh`, gateway in `api/app.py` | Hard-coded `127.0.0.1` |
| HTTP middleware hooks | `api/app.py` (`@app.middleware('http')`) | Logging and cache headers; a place to add auth |

## Order of work

Sign-in comes **before** any network access. Every phase can ship on its own.

### Phase 1: sign-in and keys (still loopback only)

1. **Owner password.** Set in Settings → Security, stored as an Argon2/scrypt hash in the config, never in plain text. Optional while the Console stays loopback-only; required to turn on sharing (Phase 2).
2. **Who must sign in:**
   - Requests from `127.0.0.1` / `::1` on the same machine: **no sign-in**, same as today. Single-PC users see no change.
   - Requests from any other address: browser users need a session; machines need a key.
3. **Browser sessions.** A sign-in page sets an HttpOnly, SameSite=Strict cookie; sessions can be revoked.
   - **CSRF:** every POST/PUT/DELETE from a remote session needs a token. Also check `Origin`, since a cookie alone isn't enough.
   - **Rate limit:** wrong passwords slow down, then lock for a few minutes.
4. **Machine keys.**
   - Settings → Security lists keys: create, name, revoke.
   - Keys are random 256-bit values, stored hashed.
   - The OpenAI gateway (`:8001`) accepts `Authorization: Bearer <key>` from other machines, just like OpenAI.
5. **Check the `api_token` that remote nodes already send.** The middleware enforces it for non-loopback requests.
6. **Dangerous routes stay local-only even after sign-in, unless enabled one by one:** `/api/shutdown`, `/api/fs/*` (file browsing), config import/export, engine install/uninstall, and anything that runs a program.

### Phase 2: "Share this Console on my local network"

1. **Setting:** Settings → Network → *Allow other computers on this network*, off by default. Turning it on asks for the owner password if none is set yet.
2. **Bind:** the Console listens on `127.0.0.1` plus the private LAN address(es) (192.168.x, 10.x, 172.16–31.x), never on public addresses.
   - It refuses requests whose source address isn't loopback or private.
   - Today the start commands are hard-coded to `127.0.0.1` (CLI, `server.ps1`, `server.sh`, Electron). They will read the host from the config instead.
3. **Windows firewall:** the installer or app adds a rule for the Console port, **Private profile only**, so it stays closed on public Wi-Fi.
   - The rule is removed when sharing is turned off.
   - Linux: document `ufw allow from 192.168.0.0/16 to any port 8900`, or similar.
4. **Discovery (mDNS / DNS-SD):** each shared Console announces `_dflash._tcp.local` with its name, version and port, using the `zeroconf` package.
   - Nodes → Add node lists Consoles found on the network, so nobody types an IP address.
   - **No address allocation is needed.** The router (DHCP) gives every machine its own IP, so every Console can use the same port (8900). Port clashes can only happen inside one machine, and startup already handles that. Discovery only finds Consoles; it never assigns addresses.
   - **Remember nodes by a permanent ID, not by IP.** Each Console gets a random `console_id` the first time it starts, and announces it. After pairing, the other side stores that ID and takes the current address from the announcements, so a DHCP address change doesn't break the node. A typed URL stays as the fallback.
   - **Duplicate names:** mDNS renames clashing service names automatically ("OMAN (2)"); users can rename their Console in Settings.
5. **Pairing (like Bluetooth):**
   - Machine A shows a 6-digit code for 2 minutes.
   - The user picks A in the list on machine B and types the code.
   - The two exchange a machine key over a short-lived handshake.
   - Afterwards B connects without anyone typing anything, and either side can revoke the key.
6. **Encryption on the LAN:** plain HTTP is acceptable at first on a home network, with a clear warning in the UI. Later:
   - each Console makes a self-signed certificate;
   - pairing stores the certificate fingerprint, so node-to-node traffic is encrypted and checked against that fingerprint.

   Browsers will still warn about the self-signed certificate; that's acceptable for the browser case at home.

### Phase 3 (later, separate decision): over the internet

Two Consoles in different homes can't reach each other directly: home routers block incoming connections, and many ISPs use CGNAT. Something in the middle is unavoidable:

| Option | For | Against |
|---|---|---|
| Port forwarding on the user's router | No extra service | Users rarely manage it, often impossible behind CGNAT, exposes the PC to the internet |
| Own relay server (like ngrok) | Full control, no third-party account | Must be hosted and paid for; all traffic passes through it |
| Embedded Tailscale library (`tsnet`) | Encrypted peer-to-peer, works through CGNAT | Still a Tailscale account, though sign-in can happen inside the app |

Requirements before any internet exposure:
- Phase 1 sign-in;
- HTTPS with a real certificate, or traffic carried inside an encrypted tunnel;
- the dangerous routes stay off.

## Tests to add

- Middleware: loopback requests pass without a session; non-loopback requests without a session or key get 401; a wrong key gets 401; a revoked key gets 401.
- CSRF: a remote POST without a token or with a foreign `Origin` gets 403.
- Dangerous routes return 403 from non-loopback addresses even with a valid session.
- Rate limit on sign-in.
- The bind only includes private addresses; a public source address is refused.
- Pairing: wrong code, expired code, a code used twice.

## Open questions for the maintainer

1. Should the browser UI ever be reachable remotely, or only Console-to-Console GPU sharing? Allowing only the second is a much smaller attack surface.
2. One owner password, or named users?
3. Should a shared Console ever allow model downloads triggered from another machine?
