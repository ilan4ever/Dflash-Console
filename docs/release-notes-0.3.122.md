## DFlash Console 0.3.122

### Fix — installer version label

The setup window no longer shows a stale **already-installed** version
(for example `0.3.86`) while copying a newer release. The title now uses
the version baked from `package.json` at installer build time.

### Test — GitHub automatic update

This release is also the follow-up build for installed **0.3.121** apps:
the desktop updater should prompt for **0.3.122** from GitHub Releases.

### Install

- **Windows:** `DFlash-Console-Setup-0.3.122-x64.exe` from GitHub Releases
- **CLI:** `pip install dflash-console==0.3.122`
