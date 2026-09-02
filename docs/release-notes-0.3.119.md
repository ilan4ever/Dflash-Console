## DFlash Console 0.3.119

### Highlights
- Automatic updates now use signed metadata from public GitHub Releases
- Update installers are downloaded directly from the matching GitHub release
- `latest.json` is published with every Windows release
- Embedded update-feed credentials are no longer required
- Legacy Hostinger update-feed publishing remains best effort

### Windows distribution
The Windows installer and portable executable are distributed free of charge.
Unsigned binaries may show an “Unknown publisher” or SmartScreen warning.
Verify the release source and `SHA256SUMS.txt` before installing.

### Install
- **Windows:** download `DFlash-Console-Setup-0.3.119-x64.exe` from GitHub Releases
- **Portable:** download `DFlash-Console-Portable-0.3.119-x64.exe`
- **CLI:** `pip install dflash-console` then `dflash serve`
