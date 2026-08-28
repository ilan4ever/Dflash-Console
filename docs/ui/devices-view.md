# Nodes view

## Layout
Full-width node manager (no sidebar inspector).

## Content
- Headline: **Remote inference nodes**
- Short intro + **Add node** button
- List of registered nodes (label, URL, status, version, actions)
- Empty state when no nodes are configured
- Footer: v1 uses manual URL registration; encrypted tunnels are future work

## Actions (v1)
- **Add node** — modal for label, base URL, optional API token
- **Check** — ping `/api/health` on the remote Console
- **Test chat** — send a short greeting through the remote gateway
- **Remove** — delete from config

## Terminal
`dflash nodes`, `dflash nodes add URL --label NAME`, `dflash nodes health NAME`,
`dflash nodes remove NAME`. See [CLI.md](../CLI.md).

## API
See [nodes-v1-plan.md](./nodes-v1-plan.md).

## Top nav title
**Nodes**
