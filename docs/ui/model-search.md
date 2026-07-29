# Model Search Modal (Hugging Face)

## Layout
- Full-screen centered modal, two panes
- **Left:** Search + staff picks list
- **Right:** Model detail + download

## Left pane
- Search: "Search local models by name or author…" (mock: searches staff picks)
- Staff picks header + Best Match sort
- List items: icon, name, one-line description, capability icons, age

## Mock staff picks
1. **Gemma 4 12B QAT** — google/gemma-4-12b-qat — Staff Pick
2. NVIDIA Nemotron 3 Nano
3. Qwen3.5 9B

## Right pane (selected model)
- Path + copy, close X
- Stats: downloads, stars, last updated
- Tags: PARAMS, ARCH, DOMAIN, FORMAT
- Capabilities: Vision, Tool Use, Reasoning
- Download card: filename, size, "Full GPU Offload Possible"
- **Download [size]** primary button
- README section (markdown preview)

## Actions (mock only)
- Copy model id
- Close modal
- Download shows toast "Mock — not wired yet"
