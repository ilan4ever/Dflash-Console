# Public repository and release guide

This project uses GitHub Issues and Discussions for public communication. The
Windows binaries are published as GitHub Release assets, not committed to the
source repository.

## 1. Configure the repository

In **Settings → General**:

1. Keep the repository **Public**.
2. Enable **Issues** and **Discussions**.
3. Enable private vulnerability reporting under **Security**.
4. Set Actions to allow workflows from this repository.
5. Protect `main` with required CI checks and disallow force-pushes.

The repository already contains issue forms, discussion forms, `SUPPORT.md`,
`SECURITY.md`, and Dependabot configuration. Discussions must be enabled in
GitHub settings or with:

```powershell
gh repo edit ilan4ever/Dflash-Console --enable-discussions
```

Recommended Discussion categories:

- Announcements — maintainer release notes
- Q&A — installation and usage questions
- Ideas — feature proposals
- Show and tell — configurations and workflows from users

## 2. Prepare a release

From a clean checkout:

```powershell
git fetch origin
git switch main
git pull --ff-only
python scripts/release-preflight.py
python -m pytest -q
node --check electron/main.js
npm audit --audit-level=high
```

Run the full production checklist (runtime bundles, dependencies, security,
packaging, docs) — see **[PRODUCTION.md](./PRODUCTION.md)**.

Update the version with the helper. It keeps the package, lockfile, backend,
About page, and README synchronized:

```powershell
.\scripts\bump-version.ps1
```

Review the generated changes, commit them, and push `main`. Do not commit
`config.json`, model weights, logs, `node_modules`, or `dist-electron`.

## 3. Publish the Windows EXE

Create a version tag that exactly matches `package.json`:

```powershell
$version = (Get-Content package.json -Raw | ConvertFrom-Json).version
git tag "v$version"
git push origin "v$version"
```

The **Windows release** workflow then:

1. Checks out the tag.
2. Installs the locked Electron dependencies.
3. Runs release preflight.
4. Builds the NSIS installer and portable EXE.
5. Generates `SHA256SUMS.txt`.
6. Creates a GitHub Release and uploads the installer, portable EXE,
   blockmap, update metadata, and checksums.

Use the NSIS installer for normal installation:

`DFlash-Console-Setup-<version>-x64.exe`

Use the portable package for a no-install run:

`DFlash-Console-Portable-<version>-x64.exe`

The binaries are unsigned by default. Sign them before distributing beyond a
trusted team if Windows SmartScreen reputation is important.

## 4. Announce and support the release

After the workflow succeeds:

1. Open the Release page and verify both EXEs and `SHA256SUMS.txt`.
2. Test the installer on a clean Windows user profile.
3. Publish a short announcement in Discussions with the release link.
4. Point questions to Discussions, bugs to Issues, and vulnerabilities to the
   private security channel.
5. Keep the release notes focused on user-visible changes and known limits.

Never upload credentials, model files, private logs, or configuration files as
release assets.
