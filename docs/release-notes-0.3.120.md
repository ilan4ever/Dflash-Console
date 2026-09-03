## DFlash Console 0.3.120

### Test release — GitHub auto-update verification

This patch release exists to verify that installed desktop apps detect and
download updates from public GitHub Releases (`latest.json` + signed setup EXE).

No user-facing feature changes are intended beyond the version bump.

### How to test

1. Install **v0.3.119** (or any older installed build).
2. Launch the desktop app and wait for the update prompt, or check **About**.
3. Confirm the offered version is **0.3.120** and the installer downloads from
   `github.com/ilan4ever/Dflash-Console/releases/download/v0.3.120/...`.

### Install

- **Windows:** `DFlash-Console-Setup-0.3.120-x64.exe` from GitHub Releases
- **CLI:** `pip install dflash-console==0.3.120`
