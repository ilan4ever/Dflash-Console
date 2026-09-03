## DFlash Console 0.3.123

### Fix — Gemma 4 31B D-Flash image chat

Gemma 4 31B D-Flash now accepts OpenAI-style `image_url` chat parts, the same
way Gemma 4 12B D-Flash already did. The existing vision projector (`mmproj`) is
wired into the llama-server preset while D-Flash speculative draft stays enabled.

Gateway and Playground now advertise `supports_vision` / `imageInput` for this
engine when an mmproj file is present.

If your local `config.json` still has `"vision": false` on the 31B engine entry,
remove that flag so the projector is not forced off.

### Install

- **Windows:** `DFlash-Console-Setup-0.3.123-x64.exe` from GitHub Releases
- **CLI:** `pip install dflash-console==0.3.123`
