'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const {
  DFLASH_APP_ID,
  DFLASH_APP_NAME,
  validateManifest,
  normalizeUpdateConfig,
} = require('../electron/update-contract');
const {
  signedManifestPayload,
  verifyManifestSignature,
} = require('../electron/update-integrity');
const { UpdateService, safeArtifactName } = require('../electron/update-service');
const {
  hasPostInstallLaunchArg,
  shouldSuppressAutomaticUpdateCheck,
} = require('../electron/update-lifecycle');
const packageJson = require('../package.json');

function manifest() {
  return {
    app: DFLASH_APP_NAME,
    appId: DFLASH_APP_ID,
    version: '0.0.74',
    fileName: 'DFlash-Console-Setup-0.0.74-x64.exe',
    sizeBytes: 10,
    sha512: 'a'.repeat(128),
    releaseNotes: 'Test release',
    downloadUrl: 'https://updates.example.test/DFlash-Console-Setup-0.0.74-x64.exe',
    publishedAt: '2026-08-06T00:00:00Z',
    signatureAlgorithm: 'RSA-SHA256',
    signatureKeyId: 'test-key',
  };
}

test('validates the DFlash x64 setup manifest contract', () => {
  const value = manifest();
  assert.equal(validateManifest(value).valid, false);
  value.signature = 'placeholder';
  assert.equal(validateManifest(value).valid, true);
  assert.equal(safeArtifactName(value.fileName), true);
  assert.equal(safeArtifactName('../' + value.fileName), false);
});

test('setup UI bakes package.json version instead of the installed folder label', () => {
  const builder = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'build-fast-installer.ps1'), 'utf8');
  const setupBuild = fs.readFileSync(path.join(__dirname, '..', 'tools', 'dflash-setup-ui', 'build.ps1'), 'utf8');
  const setupForm = fs.readFileSync(path.join(__dirname, '..', 'tools', 'dflash-setup-ui', 'SetupForm.cs'), 'utf8');
  assert.match(builder, /install-version\.txt/);
  assert.match(setupBuild, /SetupVersion\.cs/);
  assert.match(setupBuild, /package\.json/);
  assert.match(setupForm, /SetupVersion\.Value/);
  assert.match(setupForm, /already installed/);
  assert.doesNotMatch(setupForm, /foreach \(string dir in new\[\] \{ installRoot, uiRoot \}\)/);
});

test('uses the branded setup UI instead of the Windows NSIS wizard', () => {
  const targets = packageJson.build?.win?.target || [];
  assert.equal(targets.some((target) => target.target === 'nsis'), false);
  assert.equal(packageJson.scripts?.dist, 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-fast-installer.ps1');
  const builder = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'build-fast-installer.ps1'), 'utf8');
  assert.match(builder, /dflash-setup-ui\.exe/);
  // The branded UI is the executable itself with the package appended; there
  // is no silent outer wrapper that would hide the window while unpacking.
  assert.match(builder, /DFLASHPL/);
  assert.doesNotMatch(builder, /7zS2\.sfx/);
});

test('installer window appears immediately and carries its own package', () => {
  const form = fs.readFileSync(path.join(__dirname, '..', 'tools', 'dflash-setup-ui', 'SetupForm.cs'), 'utf8');
  const helper = fs.readFileSync(path.join(__dirname, '..', 'electron', 'update-helper.ps1'), 'utf8');
  const build = fs.readFileSync(path.join(__dirname, '..', 'tools', 'dflash-setup-ui', 'build.ps1'), 'utf8');
  // The UI reads its appended package and unpacks it with a bundled extractor,
  // so no system-wide 7-Zip is required and the window is visible from t=0.
  assert.match(form, /TryReadPayloadTrailer/);
  assert.match(form, /ExtractOwnPayloadArchive/);
  assert.match(form, /ExtractEmbeddedSevenZip/);
  assert.match(form, /CopyUiOnly/);
  assert.match(build, /dflash-7za-x64\.exe/);
  // The update helper runs that executable directly instead of unpacking a
  // wrapper first, which used to leave the screen empty for many seconds.
  assert.match(helper, /Test-DflashPayloadInstaller/);
  assert.match(helper, /launching it directly \(instant window\)/);
});

test('update helper extracts the setup.exe the SFX really contains', () => {
  const helper = fs.readFileSync(path.join(__dirname, '..', 'electron', 'update-helper.ps1'), 'utf8');
  // The 7zS2 SFX stores the branded UI at its root as setup.exe. Extracting
  // only dflash-setup-ui.exe failed on every in-app update, which dropped
  // /AutoInstall and showed the manual install-scope dialog instead.
  assert.match(helper, /\$bootstrapNames = @\('setup\.exe', 'dflash-setup-ui\.exe'\)/);
  assert.match(helper, /\+ \$bootstrapNames/);
  assert.match(helper, /\/AutoInstall/);
  assert.match(helper, /\$setupArgs = @\("\/Package=`"\$Path`""\)/);
  assert.match(helper, /\/InstallRoot=`"\$InstallRoot`""/);
});

test('update helper opens the installer window before the app finishes quitting', () => {
  const helper = fs.readFileSync(path.join(__dirname, '..', 'electron', 'update-helper.ps1'), 'utf8');
  const clearAt = helper.indexOf('Clear-InstallDoneFlag');
  const launchAt = helper.indexOf('$installer = Start-UpdateInstaller');
  const waitAt = helper.indexOf('Wait-Process -Id $ParentProcessId');
  assert.ok(clearAt > 0, 'install done flag is cleared before the install starts');
  assert.ok(launchAt > clearAt, 'installer starts after the stale done flag is cleared');
  assert.ok(waitAt > launchAt, 'installer must start before waiting for the old app to exit');
});

test('hides the running app while the installer takes over', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  assert.match(main, /function prepareWindowForInstall\(\)/);
  assert.match(main, /mainWindow\.hide\(\)/);
  // Two full-setup handoffs and the side-by-side "Restart to update".
  assert.equal((main.match(/prepareWindowForInstall\(\);\s*\n\s*setTimeout\(\(\) => app\.quit\(\), 100\);/g) || []).length, 3);
});

test('supports public and authenticated HTTPS update configurations', () => {
  const publicConfig = normalizeUpdateConfig({
    manifestUrl: 'https://github.com/ilan4ever/Dflash-Console/releases/latest/download/latest.json',
  });
  assert.equal(publicConfig.token, '');
  const config = normalizeUpdateConfig({
    manifestUrl: 'https://updates.example.test/manifest.json',
    token: 'test-token',
  });
  assert.equal(config.token, 'test-token');
});

test('public GitHub updates do not require a feed token', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  assert.match(main, /if \(!manifestUrl\) return null;/);
  assert.doesNotMatch(main, /if \(!manifestUrl \|\| !token\)/);
});

test('Windows startup registration uses the installed executable and startup marker', () => {
  const settings = fs.readFileSync(path.join(__dirname, '..', 'electron', 'app-settings.js'), 'utf8');
  assert.match(settings, /setLoginItemSettings/);
  assert.match(settings, /path: exe/);
  assert.match(settings, /STARTUP_ARGS/);
  assert.match(settings, /startupRegistrationState/);
});

test('persists Electron page zoom across UI reloads', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  const settings = fs.readFileSync(path.join(__dirname, '..', 'electron', 'app-settings.js'), 'utf8');
  assert.match(main, /did-finish-load/);
  assert.match(main, /zoom-changed/);
  assert.match(main, /applyStoredUiZoom/);
  assert.match(settings, /uiZoomFactor/);
});

test('does not attach authentication headers to public update requests', () => {
  const publicService = new UpdateService({
    manifestUrl: 'https://github.com/ilan4ever/Dflash-Console/releases/latest/download/latest.json',
  });
  assert.deepEqual(publicService.requestHeaders('application/json'), {
    Accept: 'application/json',
  });

  const authenticatedService = new UpdateService({
    manifestUrl: 'https://updates.example.test/manifest.json',
    token: 'test-token',
  });
  assert.deepEqual(authenticatedService.requestHeaders('application/json'), {
    Accept: 'application/json',
    Authorization: 'Bearer test-token',
  });
});

test('verifies RSA-SHA256 signatures over canonical unsigned manifests', () => {
  const keys = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const value = manifest();
  value.signature = crypto.sign('RSA-SHA256', signedManifestPayload(value), keys.privateKey).toString('base64');
  assert.equal(verifyManifestSignature(value, keys.publicKey), true);
  value.version = '0.0.75';
  assert.throws(() => verifyManifestSignature(value, keys.publicKey));
});

test('keeps file staging isolated from the final artifact until verified', async () => {
  const temp = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'dflash-update-test-'));
  assert.match(temp, /dflash-update-test-/);
  await fs.promises.rm(temp, { recursive: true, force: true });
});

test('owns the ready prompt in the dark renderer instead of native Electron dialogs', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  const settings = fs.readFileSync(
    path.join(__dirname, '..', 'static', 'js', 'app-settings-live.js'),
    'utf8',
  );
  const index = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');

  assert.doesNotMatch(main, /promptInstallUpdate|DFlash Console update ready/);
  assert.match(settings, /desktopUpdateModal/);
  assert.match(index, /id="desktopUpdateModal"/);
  // The in-app details dialog opens only from the header notice. The pop-up
  // window is the only thing that may appear on its own.
  assert.doesNotMatch(settings, /maybeShowUpdatePrompt|UPDATE_PROMPT_VERSION_KEY/);
  assert.match(settings, /appUpdateNoticeInfo'\)\?\.addEventListener\('click', openUpdateDetails\)/);
});

test('suppresses automatic update prompts during the installer relaunch handoff', () => {
  assert.equal(hasPostInstallLaunchArg(['electron', '--dflash-post-update']), true);
  assert.equal(hasPostInstallLaunchArg(['electron', '--dflash-post-install']), true);
  assert.equal(hasPostInstallLaunchArg(['electron']), false);
  assert.equal(shouldSuppressAutomaticUpdateCheck({
    argv: ['electron', '--dflash-post-update'],
  }), true);
  assert.equal(shouldSuppressAutomaticUpdateCheck({
    argv: ['electron'],
    postInstallWelcomeActive: true,
  }), true);
  assert.equal(shouldSuppressAutomaticUpdateCheck({
    argv: ['electron'],
    postInstallSetupActive: true,
  }), true);
  assert.equal(shouldSuppressAutomaticUpdateCheck({ argv: ['electron'] }), false);
});

test('wires the post-install guard into both automatic check and popup paths', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  assert.equal((main.match(/shouldSuppressAutomaticUpdateCheck\(\{/g) || []).length, 2);
  assert.match(main, /postInstallWelcomeActive/);
  assert.match(main, /postInstallSetupActive/);
});

test('Windows sign-in launch honors "Start minimized to tray"', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  // The setting only applies to the --dflash-startup launch and needs a tray
  // icon to reopen the window from.
  assert.match(main, /function shouldStartHiddenInTray\(\)/);
  assert.match(main, /isStartupLaunch\(\)/);
  assert.match(main, /settings\.startMinimized && settings\.minimizeToTray/);
  // No splash window and no visible main window on a silent tray start.
  assert.match(main, /if \(shouldStartHiddenInTray\(\)\) return;/);
  assert.match(main, /startedHiddenInTray = shouldStartHiddenInTray\(\);/);
  // Update prompts wait for the user to open the window from the tray.
  assert.match(main, /pendingUpdateManifest/);
});

function downloadFixture() {
  const payload = Buffer.from('dflash-setup-payload-for-tests');
  return {
    payload,
    manifest: {
      ...manifest(),
      sizeBytes: payload.length,
      sha512: crypto.createHash('sha512').update(payload).digest('hex'),
      signature: 'not-checked-by-download',
    },
  };
}

function fakeDownloadFetch(payload, calls) {
  return async () => {
    calls.push(Date.now());
    // Give a second caller time to arrive while this download is running.
    await new Promise((resolve) => setTimeout(resolve, 30));
    return {
      ok: true,
      status: 200,
      body: (async function* body() {
        yield payload.subarray(0, 8);
        yield payload.subarray(8);
      })(),
    };
  };
}

async function withStagingDir(run) {
  const stagingDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'dflash-update-test-'));
  try {
    await run(stagingDir);
  } finally {
    await fs.promises.rm(stagingDir, { recursive: true, force: true });
  }
}

test('background download, header and Settings share one download', () => withStagingDir(async (stagingDir) => {
  const { payload, manifest: target } = downloadFixture();
  const calls = [];
  const states = [];
  const service = new UpdateService({
    manifestUrl: 'https://updates.example.test/latest.json',
    stagingDir,
    fetch: fakeDownloadFetch(payload, calls),
    onStatus: (status) => states.push(status.state),
  });
  const [first, second] = await Promise.all([service.stageUpdate(target), service.stageUpdate(target)]);
  assert.equal(calls.length, 1, 'the installer is fetched once');
  assert.equal(first, second);
  assert.deepEqual(await fs.promises.readFile(first), payload);
  assert.equal(service.getStatus().ready, true);
  assert.equal(states.at(-1), 'ready');
  // A later call finds the verified file and does not download again.
  assert.equal(await service.stageUpdate(target), first);
  assert.equal(calls.length, 1);
}));

test('a download cut off by quitting the app does not block the next one', () => withStagingDir(async (stagingDir) => {
  const { payload, manifest: target } = downloadFixture();
  const partPath = path.join(stagingDir, `.${target.fileName}.part`);
  await fs.promises.writeFile(partPath, 'half a file');
  const service = new UpdateService({
    manifestUrl: 'https://updates.example.test/latest.json',
    stagingDir,
    fetch: fakeDownloadFetch(payload, []),
  });
  const staged = await service.stageUpdate(target);
  assert.deepEqual(await fs.promises.readFile(staged), payload);
  assert.equal(fs.existsSync(partPath), false);
}));

test('a failed download can be retried', () => withStagingDir(async (stagingDir) => {
  const { payload, manifest: target } = downloadFixture();
  let attempt = 0;
  const service = new UpdateService({
    manifestUrl: 'https://updates.example.test/latest.json',
    stagingDir,
    fetch: async () => {
      attempt += 1;
      if (attempt === 1) return { ok: false, status: 503, body: null };
      return { ok: true, status: 200, body: (async function* body() { yield payload; })() };
    },
  });
  await assert.rejects(service.stageUpdate(target), /503/);
  const staged = await service.stageUpdate(target);
  assert.deepEqual(await fs.promises.readFile(staged), payload);
}));

function readSource(...parts) {
  return fs.readFileSync(path.join(__dirname, '..', ...parts), 'utf8');
}

test('updates download in the background before anyone is asked', () => {
  const main = readSource('electron', 'main.js');
  const start = main.indexOf('async function checkAndDownloadUpdate()');
  const body = main.slice(start, main.indexOf('\n}\n', start));
  const stageAt = body.indexOf('await updateService.stageUpdate(manifest)');
  const announceAt = body.lastIndexOf('announceUpdateReady(manifest)');
  assert.ok(stageAt > 0, 'the automatic check downloads the installer');
  assert.ok(announceAt > stageAt, 'the user hears about the update only once it is downloaded');
  assert.doesNotMatch(body, /showUpdatePopup\(/, 'the pop-up is shown only through announceUpdateReady');
  assert.match(body, /UPDATE_DOWNLOAD_RETRY_MS/, 'a failed download is not fetched again every minute');
});

test('the user can keep update news in the header only', () => {
  const main = readSource('electron', 'main.js');
  const index = readSource('static', 'index.html');
  const settings = readSource('static', 'js', 'app-settings-live.js');
  const popup = readSource('electron', 'update-popup.html');
  const announce = main.slice(main.indexOf('function announceUpdateReady('));
  assert.match(announce.slice(0, 300), /updateNotice === 'header'\) return;/);
  // A tray start keeps its pending prompt, and that prompt obeys the setting.
  assert.match(main, /pendingUpdateManifest = null;\s*\n\s*announceUpdateReady\(manifest\);/);
  assert.match(index, /id="appSettingsUpdateNotice"/);
  assert.match(index, /<option value="header">Header notice only<\/option>/);
  assert.match(index, /id="appUpdateNotice"/);
  assert.match(index, /id="appUpdateNoticeInstall"[^>]*>Upgrade now</);
  assert.match(settings, /savePatch\(\{ updateNotice:/);
  assert.match(settings, /api\.installUpdateNow\(\)/);
  assert.match(popup, /setAppSettings\(\{ updateNotice: 'header' \}\)/);
});

test('the update notice setting only takes known values', () => {
  const { DEFAULTS, normalizeUpdateNotice } = require('../electron/app-settings');
  assert.equal(DEFAULTS.updateNotice, 'popup');
  assert.equal(normalizeUpdateNotice('header'), 'header');
  assert.equal(normalizeUpdateNotice('popup'), 'popup');
  assert.equal(normalizeUpdateNotice('banner'), 'popup');
  assert.equal(normalizeUpdateNotice(undefined), 'popup');
});

test('the update pop-up is a small corner card, not a 500x460 window', () => {
  const main = readSource('electron', 'main.js');
  const width = Number(main.match(/const UPDATE_POPUP_WIDTH = (\d+);/)?.[1]);
  const maxHeight = Number(main.match(/const UPDATE_POPUP_MAX_HEIGHT = (\d+);/)?.[1]);
  assert.ok(width > 0 && width <= 0.75 * 500, `width ${width}`);
  assert.ok(maxHeight > 0 && maxHeight <= 0.8 * 460, `max height ${maxHeight}`);
  assert.doesNotMatch(main, /width: 500,\s*\n\s*height: 460/);
  assert.match(main, /frame: false/);
  assert.match(main, /ipcMain\.handle\('update-popup:fit'/);
  assert.match(main, /event\.sender !== updatePopupWindow\.webContents/);
});
