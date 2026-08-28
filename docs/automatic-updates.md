# DFlash Console automatic updates

The Windows desktop shell checks the signed feed at:

`https://onevoiceai.in/?dflash-console-update=latest`

The feed and installer are protected by a token and stored outside the website's public directory. The installer is accepted only when the manifest has a valid DFlash app ID, RSA-SHA256 signature, filename, size, and SHA-512 digest. The setup artifact is a branded DFlash 7-Zip SFX wrapper that launches the native dark setup UI; it is not an NSIS or Windows wizard.

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

GitHub releases use `DFLASH_UPDATE_TOKEN`, `DFLASH_UPDATE_PRIVATE_KEY`, `HOSTINGER_UPDATE_ROOT`, and the Hostinger SSH secrets. Windows code-signing secrets `WINDOWS_CSC_LINK` and `WINDOWS_CSC_KEY_PASSWORD` are required for the official GitHub Release. The detached helper waits for the running app and its child processes to quit, then opens the verified branded DFlash setup UI. The installer provides the per-user/per-machine choice, shows custom progress, and relaunches the app after installation.
