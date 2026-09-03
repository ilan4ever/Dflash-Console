## DFlash Console 0.3.121

### Fix — GitHub automatic updates

Installed builds now check the public GitHub `latest.json` feed even when no
update-feed token is configured. 0.3.119 and 0.3.120 skipped the check entirely
on the public feed, so no update popup appeared.

### How to test

1. Install this build once (older public builds cannot self-update).
2. After the next release, the installed app should prompt for the update.

### Install

- **Windows:** `DFlash-Console-Setup-0.3.121-x64.exe` from GitHub Releases
- **CLI:** `pip install dflash-console==0.3.121`
