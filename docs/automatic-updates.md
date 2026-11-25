# DFlash Console automatic updates

The Windows desktop shell checks the signed GitHub Release manifest at:

`https://github.com/ilan4ever/Dflash-Console/releases/latest/download/latest.json`

The manifest is publicly downloadable after the repository is made public. The installer is accepted only when the manifest has a valid DFlash app ID, RSA-SHA256 signature, filename, size, and SHA-512 digest. The setup artifact is the branded DFlash setup UI itself with the compressed app package appended, so the window appears immediately and shows its own extraction progress; it is not an NSIS or Windows wizard and not a silent wrapper.

## Release prerequisites

Keep the RSA private key outside the repository. The committed public key is `electron/resources/update-manifest-public.pem`. For local publishing, provide Hostinger SSH settings and `HOSTINGER_UPDATE_ROOT` in `.env.admin` and run:

```powershell
.\scripts\bump-version.ps1
.\scripts\run-electron.ps1 -Build
$version = node -p "require('./package.json').version"
node tools/sign-update-manifest.js `
  --installer "dist-electron/DFlash-Console-Setup-$version-x64.exe" `
  --output "dist-electron/latest.json" `
  --version $version `
  --download-url "https://onevoiceai.in/?dflash-console-update=download"
.\scripts\publish-dflash-update.ps1 `
  -Installer "dist-electron/DFlash-Console-Setup-$version-x64.exe" `
  -Manifest "dist-electron/latest.json" `
  -Token "<the feed token>"
```

GitHub releases use `DFLASH_UPDATE_PRIVATE_KEY` to sign `latest.json`. The legacy Hostinger feed can still be published with `DFLASH_UPDATE_TOKEN`, `HOSTINGER_UPDATE_ROOT`, and the Hostinger SSH secrets, but it is not required by the GitHub updater. Windows code-signing secrets `WINDOWS_CSC_LINK` and `WINDOWS_CSC_KEY_PASSWORD` are optional. The detached helper waits for the running app and its child processes to quit, then opens the verified branded DFlash setup UI. The installer provides the per-user/per-machine choice, shows custom progress, and relaunches the app after installation.

## Side-by-side installs (from release A)

The program folder holds one `app-<version>` folder per version and a small launcher at the old `DFlash Console.exe` path. `current.txt` names the version the launcher starts.

When an update is downloaded and verified, the running app runs `setup.exe /Prepare` to unpack the new version next to itself, then points `current.txt` at it and shows "Restart to update". The restart is a normal quit and start; the setup window is not shown.

If a new version fails to start twice, the launcher goes back to `previous.txt`, and the app restores the settings saved before the switch. Per-machine installs, failed preparations, and the "Use the full setup" button in Settings still go through the setup window as described above.

Design and test plan: [plans/side-by-side-updates.md](plans/side-by-side-updates.md).
