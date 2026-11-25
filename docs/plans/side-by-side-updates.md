# Side-by-side updates: install in the background, restart in seconds

Status: **release A built, not released.** Ilan approved the design and all four decisions on 2026-11-25. Release A (branch `feat/side-by-side-updates`) needs the end-to-end test on a separate Windows machine before it is published. See "Release A: what was built" at the end.
Builds on PR #27 (background download, header notice, optional pop-up).

## Goal

When a new release comes out, the app downloads it **and installs it** in the background while the user keeps working. Nothing visible happens until the user is told "Restart to update". The restart takes a few seconds instead of running the setup window.

- The running app is never touched while the new version is being prepared. No locked files, no half-updated code, no "code 11".
- If the new version fails to start, the app goes back to the previous version by itself.
- The full setup window stays for first installs, per-machine installs and repairs, and as the fallback whenever the fast path fails.

Not in scope here: keeping models loaded across an app restart. Today, quitting the app releases its engines (`DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN`), so any restart unloads models. That is step 3, a separate design. With this plan the restart is short, but models still reload afterwards.

## Today

| Part | Where | What an update does |
|---|---|---|
| Electron app (~430 MB) | `%LOCALAPPDATA%\Programs\DFlash Console\` (per-user) or `Program Files\DFlash Console\` (per-machine) | The setup window closes the app, then copies every file over the old ones |
| Backend code: `api`, `core`, `static`, `scripts`, `server.ps1`… (~20 MB) | Console data folder (`%USERPROFILE%\DFlash Console\`) | Copied from the app's `resources\console-runtime` on the first start of a new version (`ensureRuntimeTree`) |
| Python packages (`.venv`) | Data folder | `server.ps1` runs `pip install` at start when `requirements.lock` changed |
| Engines (llama.cpp, vLLM, Transformers), models, config | Data folder | Not part of the app release; updated separately |

The slow, fragile step is the first one: overwriting a folder the app was just running from.

## Design

### 1. Versioned app folders and a small launcher

```
%LOCALAPPDATA%\Programs\DFlash Console\
  DFlash Console.exe          ← launcher (new, ~100 KB); same name and path as today
  current.txt                 ← "0.3.301": the version the launcher starts
  previous.txt                ← "0.3.300": where to go back to if 0.3.301 fails
  app-0.3.300\                ← a complete Electron app, exactly what is installed today
  app-0.3.301\
  Uninstall DFlash Console.exe
```

- The **launcher** reads `current.txt` and starts `app-<version>\DFlash Console.exe`, passing on its arguments (`--dflash-startup` and the rest), then exits. It is written in C# and built with `csc` like the setup UI, so it adds no new dependency.
- The launcher keeps the **exact path** of today's exe. Desktop and Start-menu shortcuts, the Start with Windows entry and the Apps & features entry all keep working unchanged.
- The **switch to a new version** is one small file write: `current.txt` is written to a temporary file and renamed over the old one, which is atomic on NTFS. No folder is renamed, and no running file is replaced.

This is how Discord, Slack and other Squirrel-based apps update on Windows.

**Rejected alternative:** keeping today's flat layout and renaming whole folders at restart (`DFlash Console` → `.old`, `.next` → `DFlash Console`). It needs no launcher, but on Windows a folder rename fails whenever anything holds a handle inside it: an Explorer window, antivirus, the search indexer. A power cut between the two renames would also leave no app at all, with every shortcut broken. A pointer file has neither problem.

### 2. Prepare in the background

After PR #27's download is verified (signature, size, SHA-512):

1. Unpack the app into `app-<version>.partial\`. The setup exe does this in a new **windowless** mode, `setup.exe /Prepare /Target <folder>`, reusing its existing 7-Zip extraction code. It runs at below-normal CPU and disk priority, so engines and downloads don't feel it.
2. Check that `DFlash Console.exe` and `resources\console-runtime\server.ps1` exist, then write `install-version.txt`.
3. Set folder access on the new folder only, without walking the whole tree (as PR #26 does for the data folder).
4. Python packages: if the new `requirements.lock` differs from the running one, run `pip download -r requirements.lock` into `<data>\wheelhouse\<hash>\`. Nothing is installed into the running `.venv`. **Not in release A** (see the end).
5. Rename `app-<version>.partial` to `app-<version>`. Only a complete folder ever has a final name.
6. Write `previous.txt` (the running version), then `current.txt` (the new one).
7. Announce "Restart to update" using PR #27's header notice and optional pop-up.

If any step fails, the half-made folder is deleted and the app falls back to today's flow (the "Upgrade now" button runs the setup window). An app killed mid-way leaves a `.partial` folder, which is deleted at the next start.

### 3. Restart

**Restart to update** = `app.relaunch()` pointed at the launcher, then a normal quit:

1. The app shuts down as it does today (engines released, backend server stopped).
2. The launcher starts `app-0.3.301`.
3. The new version's first start copies its backend code into the data folder. It already does this today; it takes a second or two.
4. If `requirements.lock` changed, `server.ps1` first installs from the local wheelhouse (`--no-index --find-links`), which is fast and works offline. It falls back to the online install as today.

No setup window, no admin prompt, no copying 430 MB.

If the user doesn't restart, the new version starts the next time the app opens (from the tray's Quit, a reboot, or Start with Windows). This is how Chrome and VS Code behave.

### 4. Going back automatically

- The launcher counts starts of a new version in `app-<version>\launch-attempts`.
- The new app writes `app-<version>\started-ok` once its window has loaded and the backend has answered `/api/health`.
- If a version without `started-ok` has been started twice, the launcher switches `current.txt` back to `previous.txt`, marks the failed version `bad` (never chosen again), and starts the previous version.
- **Settings:** before a new version's first start, the app copies `config.json` and `auth.json` to `<data>\backups\pre-<version>\`. A version started after a rollback restores them, so a config already changed by the newer version can't break the older one. Rule for future releases: a config change must still load in the previous version, and the backup is the safety net.
- **Cleanup:** after a successful start, keep the current and previous app folders and delete older ones. That is about 430 MB extra on disk, the price of an instant rollback.

### 5. Code that must learn about the launcher

- Start with Windows (`resolveStartupExe`) must register the launcher, not `app-<version>\…`.
- `relaunchPath` in the update code becomes the launcher.
- **Taskbar pins:** a window pinned to the taskbar would point at `app-<version>\DFlash Console.exe` and break after an update. Set `BrowserWindow.setAppDetails({ appId, relaunchCommand })` to the launcher; the app already sets `setAppUserModelId('com.dflash.console')`.
- The setup UI's "close the running app" (`GetProcessesByName("DFlash Console")`) still works, because launcher and app share the name and the launcher exits at once.
- The uninstaller already deletes the whole program folder, which includes the versioned folders.

### 6. Moving existing installs to the new layout

The installed 0.3.30x versions only know the setup-window update. So the change ships in two releases:

- **Release A** ships the launcher, and its setup installs the new layout. Existing users reach it through today's flow: setup window, one last time. Setup moves the old flat files out, installs `app-A\` and the launcher, and keeps every shortcut and registry path. On a failed copy it keeps the current Retry behaviour (PR #23).
- **Release B** is the first side-by-side update. It can only be published after release A has been tested end to end.

### 7. Per-machine installs

Program Files can't be written without an admin prompt. For now, per-machine installs keep PR #27's flow: background download, then "Upgrade now" runs the setup window with its admin prompt. They can get the same treatment later through a small elevated service. That is not worth it until someone actually uses "All users on this PC".

## What the user sees

The header notice from PR #27 shows each stage. Nothing opens by itself before the update is ready:

| Stage | Header notice | Pop-up (if turned on) |
|---|---|---|
| Downloading | "Downloading update · 40%" with a thin progress bar | — |
| Installing (preparing the new folder) | "Installing update…" | — |
| Ready | "Version X ready · **Restart to update**" | "Update ready. Restart to finish; it takes a few seconds." plus **Restart to update** and Later |
| Fast path failed | "Version X ready · **Upgrade now**" (runs the setup window, as in PR #27) | Same, with the setup-window wording |

- The pop-up also gets a small "Install with the full setup instead" link. It runs the already-downloaded setup window, as a way out if the fast path ever misbehaves.
- The "a few seconds" wording must stay true. The app window is back in seconds, but until step 3 is done, models that were loaded reload after the restart. The pop-up says so when a model is loaded: "Loaded models will reload."

## What doesn't change

- Signed manifest, SHA-512 check, GitHub feed, release CI.
- The data folder, engines, models and `config.json` location.
- Settings → Desktop app, and the header notice and pop-up from PR #27 (only the wording changes to "Restart to update").

## Tests

**Automatic (CI):**
- Launcher: pick `current.txt`; fall back when it's missing or invalid; skip `bad` and `.partial` folders; roll back after two failed starts; pass arguments through. These run the built launcher against fake app folders in a temp dir, from `node --test`.
- Prepare: a `.partial` folder is removed; a failed step leaves no final folder and falls back to the setup window; the pointer is written last.
- Startup registration and relaunch point at the launcher.

**End to end, on the test machine (OMAN Windows or another PC), before release A or B is published:**
1. Install 0.3.300 (flat layout), then update to release A through the setup window. Check that the layout migrated and that shortcuts, Start with Windows, the taskbar pin and uninstall all work.
2. A → B side by side. Check that preparing happens while a model is loaded and chatting, with no visible effect; that the restart takes seconds; and that B runs.
3. A deliberately broken B′ (crashes at start). Check that the launcher goes back to A on its own and A's config is intact.
4. Kill the app during preparation, then check the next start cleans up and retries.
5. A full disk during preparation: the app falls back to the setup window and nothing breaks.
6. A per-machine install still updates through the setup window.

## Decisions (approved by Ilan, 2026-11-25)

1. **When the new version takes over:** at the next start, even if the user ignores the notice, as Chrome does.
2. **Old versions kept for rollback:** one (about 400 MB).
3. **Per-machine installs** stay on the setup window for now.
4. **Ship order:** release A (launcher + migration) on its own, with the end-to-end test, before any side-by-side update.

## Release A: what was built

| Piece | Where |
|---|---|
| Layout rules shared by setup and launcher (pointers, choosing a version, rollback after two failed starts, removing the flat install, cleanup) | `tools/dflash-setup-ui/AppLayout.cs` |
| Launcher, with the app icon; `DFLASH_LAUNCHER_DRY_RUN=<file>` records its choice instead of starting (tests) | `tools/dflash-launcher/` |
| Setup installs into `app-<version>`, puts the launcher at the root exe path, removes the old flat files, writes the pointers, keeps one previous version | `tools/dflash-setup-ui/SetupForm.cs` |
| `setup.exe /Prepare /Target=… /Result=…`: windowless unpack at below-normal priority, inside the install folder | `tools/dflash-setup-ui/PrepareMode.cs` |
| The launcher travels in the package at `resources\launcher\` | `scripts/build-fast-installer.ps1` |
| App side: prepare after the download, switch, "Restart to update", good-start marker, settings backup and restore after a rollback, cleanup, launcher for Start with Windows and taskbar pins | `electron/side-by-side.js`, `electron/main.js`, `electron/app-settings.js` |
| Header stages (downloading %, installing, ready), restart wording, "Use the full setup" in Settings | `static/js/app-settings-live.js`, `electron/update-popup.html` |

**Measured on DEVELOPER:** `/Prepare` from a real 0.3.300 installer unpacked 392 MB (599 files) in 3.4 s. The real launcher then picked the new version.

**Differences from the design above:**
- **No pip wheelhouse yet.** `requirements.lock` rarely changes. When it does, `server.ps1` installs the packages at the first start, as it does today. Add the wheelhouse if a release changes dependencies often.
- **"Loaded models reload" is always mentioned** in the restart message, not only when a model is loaded.
- **The full-setup way out is a Settings button** ("Use the full setup"), not a link in the pop-up. That keeps the pop-up small.
- **Who checks the version started:** the app writes `started-ok` once its window has loaded the Console UI from its own backend.

**Not tested on DEVELOPER, on purpose:** a full install or update. Setup writes the real shortcuts, the Apps & features entry and the live data folder, and the packaged app closes any other running DFlash Console. The end-to-end list under "Tests" is for the separate machine.
