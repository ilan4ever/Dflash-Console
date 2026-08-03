# Settings — Model defaults

There is no separate Model Defaults panel in the current build. Runtime
defaults are configured per engine in `config.json` and can be adjusted from
the engine runtime inspector before loading:

- Context size
- GPU layers, CPU threads, and batch settings
- Flash attention and related load settings
- Temperature, top-p, top-k, repeat penalty, and max tokens

The Console applies these values through the engine load and inference APIs.
Image resizing, memory guardrail profiles, and global model defaults remain
future work.
