## DFlash Console 0.3.128

### Fix — Windows startup and updates

Installed Windows builds now repair their login registration using the actual
installed executable and the `--dflash-startup` marker. The Settings page also
reports whether Windows has an active startup registration.

The desktop updater continues to use the signed public GitHub release feed and
shows the update prompt when a newer release is available.

### Fix — DFlash stacks

DFlash profiles now require a matching target and draft accelerator. Missing or
incompatible drafts open the repair flow instead of silently loading without
acceleration.

### Install

- **Windows:** `DFlash-Console-Setup-0.3.128-x64.exe` from GitHub Releases
- **CLI:** `pip install dflash-console==0.3.128`
