# Going public — pre-flight checklist

Use this before changing **Settings → General → Change repository visibility** to
**Public**. The repo can stay **private** while you preview Releases, README, and
Issues on GitHub.

## Security audit (done locally)

| Item | Status |
|------|--------|
| `config.json` | Gitignored — never commit |
| `.env.admin` | Gitignored — Hostinger SSH + update token |
| `electron/resources/update-endpoint.json` | Gitignored — copy from `update-endpoint.json.example` |
| `dist-electron/` | Gitignored — installers uploaded to GitHub Releases only |
| `scripts/deployment/config.production-deploy.json` | Gitignored — LAN IPs/SSH key path |
| `tmp-*` scratch files | Gitignored and removed from the working tree |
| Update signing | Only `update-manifest-public.pem` is committed; private key stays in CI secrets |

Run before every public push:

```powershell
git grep -iE "password|BEGIN (RSA |OPENSSH )?PRIVATE|DFLASH_UPDATE_TOKEN\s*=" -- ':!*.example' ':!.env.admin.example'
```

Do not commit API dumps, health JSON, or machine-specific paths.

## GitHub repository settings (when ready)

1. **Settings → General → Danger zone → Change visibility → Public**
2. Enable **Issues** and **Discussions** (see [RELEASING.md](./RELEASING.md))
3. **Security → Private vulnerability reporting** — enable
4. Confirm **Actions** secrets exist for automated releases (optional):
   - `DFLASH_UPDATE_TOKEN`, `DFLASH_UPDATE_PRIVATE_KEY`
   - `WINDOWS_CSC_LINK`, `WINDOWS_CSC_KEY_PASSWORD`
   - Hostinger SSH secrets (update feed publish from CI)

## Releases vs automatic updates

| Channel | Audience | Notes |
|---------|----------|-------|
| **GitHub Releases** | New downloads, public once repo is public | Setup EXE ~100 MB; fast CDN |
| **Hostinger update feed** | Installed desktop apps | Token-protected manifest; unchanged when repo goes public |

## Publish a release (private repo test)

Collaborators can download assets while the repo is private.

**Option A — tag + CI** (needs secrets on GitHub):

```powershell
$v = (Get-Content package.json -Raw | ConvertFrom-Json).version
git tag "v$v"
git push origin "v$v"
```

**Option B — manual upload** (local build):

```powershell
$v = (Get-Content package.json -Raw | ConvertFrom-Json).version
gh release create "v$v" `
  --title "DFlash Console v$v" `
  --notes-file "docs/release-notes-v$v.md" `
  "dist-electron/DFlash-Console-Setup-$v-x64.exe"
```

## After going public

- README **Download** link works for everyone
- Rotate `DFLASH_UPDATE_TOKEN` if it was ever committed (history scan)
- Review open Issues/Discussions defaults and branch protection on `main`
- **Project board:** https://github.com/users/ilan4ever/projects/1 (Bugs / triage)
- **Announcement:** post in Discussions → Announcements (`docs/announcements/public-preview.md`)
- **PyPI:** https://pypi.org/project/dflash-console/ (`pip install dflash-console`)
