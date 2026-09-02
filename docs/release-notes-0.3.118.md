## DFlash Console 0.3.118

### Highlights
- Free open-source Windows distribution with optional code signing
- Unsigned installers are published when no Windows certificate is configured
- Automatic DFlash draft discovery, validation, registration, and attachment
- FreeToken support for large Hugging Face models with live loading progress
- Reliable model catalog cleanup, organization, routing, and engine status

### Windows distribution
The Windows installer and portable executable are distributed free of charge.
When no trusted Windows code-signing certificate is configured, Windows may show
an “Unknown publisher” or SmartScreen warning. Users should verify the release
source and checksums before installing.

### Install
- **Windows:** download `DFlash-Console-Setup-0.3.118-x64.exe` from GitHub Releases
- **Portable:** download `DFlash-Console-Portable-0.3.118-x64.exe`
- **CLI:** `pip install dflash-console` then `dflash serve`

Requires Windows 10+, Python 3.10+, and PowerShell 7+ for the Console data root.
