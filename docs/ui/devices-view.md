# Nodes view

## Layout
Full-width node manager (no sidebar inspector).

## Content
- Headline: **Remote inference nodes**
- Intro explains that another Console’s GPU can appear in the model list
- **Connect securely** and **Add node**
- **Use this computer’s GPU** is on by default when you add a node
- List of nodes (label, URL, status, version, GPU sharing, actions)
- Empty state points people at adding a computer so its GPU can be used here

## Actions
- **Add node** — name, Console URL, optional token, and **Use this computer’s GPU**
- **Share GPU** / **Stop sharing GPU** — show or hide that GPU in the model list
- **Check** — ping `/api/health` on the remote Console
- **Test chat** — send a short greeting through the remote gateway
- **Remove** — delete from config

When sharing is on, GPU lists from this Console include that machine’s GPUs.
The display name is the GPU plus the node name in brackets, for example
`TITAN (lab)`. Loading that choice runs the model on the other computer.
See [SHARE-A-GPU.md](../SHARE-A-GPU.md).

## Terminal
`dflash nodes`, `dflash nodes add URL --label NAME`, `dflash nodes health NAME`,
`dflash nodes remove NAME`. See [CLI.md](../CLI.md).

## API
See [nodes-v1-plan.md](./nodes-v1-plan.md).

## Top nav title
**Nodes**
