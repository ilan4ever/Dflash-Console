## DFlash Console 0.3.133

### Fix — installed engine path resolution

- Resolve llama-server from the configured model-library checkout when the
  installed Console data root does not contain the engine.
- Keep engine capability checks consistent with the Console startup fallback.
- Add regression coverage for installed data roots using an external engine.

### Install

- **Windows:** `DFlash-Console-Setup-0.3.133-x64.exe` from GitHub Releases
- **CLI:** `pip install dflash-console==0.3.133`
