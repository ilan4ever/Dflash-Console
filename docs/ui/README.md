# DFlash Studio UI — Design Plan

Reference: LM Studio 0.4.20 screenshots. Mockup only until wired to API.

## Views (top tabs)

| Tab | Doc | Status |
|-----|-----|--------|
| Chat | *(v1.1)* | Placeholder |
| Server | inline in `index.html` | Mockup done |
| Models | [models-view.md](./models-view.md) | Mockup |
| Devices | [devices-view.md](./devices-view.md) | Mockup |
| Settings (gear) | settings/*.md | Modal mockup |

## Settings sections

| Section | Doc |
|---------|-----|
| General | [settings-general.md](./settings-general.md) |
| Appearance | [settings-appearance.md](./settings-appearance.md) |
| Developer | [settings-developer.md](./settings-developer.md) |
| Chat | [settings-chat.md](./settings-chat.md) |
| Model Defaults | [settings-model-defaults.md](./settings-model-defaults.md) |
| Integrations | [settings-integrations.md](./settings-integrations.md) |
| LM Link | [settings-lm-link.md](./settings-lm-link.md) |
| Runtime | [settings-runtime.md](./settings-runtime.md) |
| Hardware | [settings-hardware.md](./settings-hardware.md) |

## Modals

| Modal | Doc |
|-------|-----|
| Model Search (HF) | [model-search.md](./model-search.md) |

## Layout rules

- App fills available width (`100%`, no fixed pixel width on root).
- Main + inspector: `minmax(0, 1fr)` + `minmax(280px, 380px)`; inspector never clipped by viewport.
- Below 900px: inspector stacks under main (not hidden).
- Mock data only; replace with `/api/*` later.
