# DFlash Console v0.3.208

## Engines and GPU monitoring

- **Engine standby** — the Running toggle gates Console load/chat; external API clients receive HTTP 503 with a clear message when the pipeline is off.
- **External GPU cards** — detect models loaded by OneVoice, LM Studio, Ollama, and similar apps; cards flip to READY when the model is serving (no infinite Loading state).
- **Other GPU processes** — per-process VRAM chips for non-model GPU apps (Windows counters when available).
- **Unload reliability** — Unload on engine cards works while the card list refreshes.
- **Mobile layout** — compact engine cards; external models and Other GPU sections stack cleanly on narrow screens.
- **Instant load feedback** — loading card appears as soon as you press Load.

## Settings and VRAM

- **GPU performance mode** — Balanced, Performance, Inference, or Power (`hardware_settings.gpu_performance_mode`).
- **External GPU scan** — toggle with `hardware_settings.detect_external_gpu_loads`.
- **Load preflight** — `unload_first` hints when VRAM is tight; improved co-resident 12B + 31B planning on one GPU.
- **Engines card filter** — `ui_layout.engines_card_filter` (`both` / `console` / `external`).

## Integrations

- **Client identity** — `X-DFlash-Client` header shows Active client on loaded cards.
- **Model catalog** — local Hugging Face index for instant search while background refresh runs.

## Install

- Windows: [GitHub Releases](https://github.com/ilan4ever/Dflash-Console/releases/tag/v0.3.208)
- PyPI: `pip install dflash-console==0.3.208`
