# DFlash Console UI documentation

This directory records the user-facing UI contract and the settings that are
implemented in the current Console. The browser and Electron shell serve the
same `static/` application, so the views and documentation are shared.

**End-user docs:** [USER-GUIDE.md](../USER-GUIDE.md) · in-app
**Documentation → User guide** · in-app **About**

## Views

| View | Implementation | Notes |
|------|----------------|-------|
| Playground | `static/index.html` + `static/js/chat-live.js` | Local chat and model selection |
| Engines | `static/index.html` + `static/js/server-live.js` | Router lifecycle, loading, stats, and logs |
| Models | [models-view.md](./models-view.md) | Local library, DFlash stacks, filters, and loading |
| Nodes | [devices-view.md](./devices-view.md) | Planned remote-node surface; disabled in this build |
| Model catalog | [model-search.md](./model-search.md) | Hugging Face search, detail, README, and downloads |
| Settings | settings/*.md | Workspace, locations, hardware, gateway, presets, and MCP |
| Documentation | `core/api_catalog.py` + `docs/USER-GUIDE.md` | API reference and user guide |
| About | `static/index.html` + `static/js/about-live.js` | Attribution, release metadata, links, and boundaries |

## Settings sections

| Section | Doc |
|---------|-----|
| Model storage / Locations | [settings-general.md](./settings-general.md) |
| Hardware | [settings-hardware.md](./settings-hardware.md) |
| Network / Runtime / Presets | [settings-runtime.md](./settings-runtime.md) |
| MCP & clients | [settings-integrations.md](./settings-integrations.md) |
| Developer diagnostics | [settings-developer.md](./settings-developer.md) |
| Appearance | [settings-appearance.md](./settings-appearance.md) |
| Playground | [settings-chat.md](./settings-chat.md) |
| Model defaults | [settings-model-defaults.md](./settings-model-defaults.md) |
| Remote nodes | [settings-lm-link.md](./settings-lm-link.md) |

## Catalog and dialogs

| Modal | Doc |
|-------|-----|
| Model catalog (Hugging Face) | [model-search.md](./model-search.md) |

## Runtime rules

- App fills available width (`100%`, no fixed pixel width on root).
- Main + inspector: `minmax(0, 1fr)` + `minmax(280px, 380px)`; inspector never clipped by viewport.
- Below 900px: inspector stacks under main (not hidden).
- The UI talks to the local FastAPI service through `/api/*`.
- Static UI changes are picked up by the development reload watcher.
- The Electron shell points at the selected external Console data root and does
  not bundle model files or the backend.
