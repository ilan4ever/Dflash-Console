'use strict';

// Side-by-side updates (docs/plans/side-by-side-updates.md): the app-side
// layout module, the shared C# layout rules, the real launcher, and the real
// setup.exe /Prepare mode. The Windows parts build with csc and are skipped
// where it is missing.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { execFileSync, spawnSync } = require('node:child_process');
const sbs = require('../electron/side-by-side');

const repo = path.join(__dirname, '..');
const csc = [
  path.join(process.env.WINDIR || 'C:\\Windows', 'Microsoft.NET', 'Framework64', 'v4.0.30319', 'csc.exe'),
  path.join(process.env.WINDIR || 'C:\\Windows', 'Microsoft.NET', 'Framework', 'v4.0.30319', 'csc.exe'),
].find((candidate) => process.platform === 'win32' && fs.existsSync(candidate));
const windowsOnly = { skip: csc ? false : 'needs Windows and .NET Framework csc.exe' };

function tempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function makeApp(root, version, extra = {}) {
  const dir = sbs.appDir(root, version);
  fs.mkdirSync(path.join(dir, 'resources', 'launcher'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'resources', 'console-runtime'), { recursive: true });
  fs.writeFileSync(path.join(dir, sbs.EXE_NAME), 'MZ app');
  fs.writeFileSync(path.join(dir, 'resources', 'launcher', sbs.EXE_NAME), extra.launcher || 'MZ launcher');
  fs.writeFileSync(path.join(dir, 'resources', 'console-runtime', 'server.ps1'), '#');
  return dir;
}

function makeRoot(running = '0.3.300') {
  const root = tempDir('dflash-sbs-');
  makeApp(root, running);
  fs.writeFileSync(path.join(root, sbs.EXE_NAME), 'MZ launcher');
  sbs.writePointer(root, sbs.CURRENT_FILE, running);
  return { root, layout: sbs.detectLayout(path.join(sbs.appDir(root, running), sbs.EXE_NAME)) };
}

// ---------------------------------------------------------------- app side

test('the app knows when it runs from a versioned folder', () => {
  const { root, layout } = makeRoot();
  try {
    assert.equal(layout.version, '0.3.300');
    assert.equal(layout.root, root);
    assert.equal(layout.launcherPath, path.join(root, sbs.EXE_NAME));
    // The old flat install, a portable copy and the checkout have no layout.
    assert.equal(sbs.detectLayout(path.join(root, sbs.EXE_NAME)), null);
    assert.equal(sbs.detectLayout(path.join(root, 'app-junk', sbs.EXE_NAME)), null);
    assert.equal(sbs.canPrepareInPlace(layout), true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('preparing installs beside the running version and switches the pointer', async () => {
  const { root, layout } = makeRoot();
  try {
    const calls = [];
    const finalDir = await sbs.prepareVersion(layout, {
      installerPath: 'C:\\staged\\setup.exe',
      version: '0.3.301',
      runPrepare: async (installer, target) => {
        calls.push([installer, target]);
        assert.ok(target.endsWith('app-0.3.301.partial'), 'unpacks into a .partial folder first');
        makeApp(root, '0.3.301.partial-tmp', { launcher: 'MZ launcher v2' });
        fs.renameSync(sbs.appDir(root, '0.3.301.partial-tmp'), target);
      },
    });
    assert.equal(finalDir, sbs.appDir(root, '0.3.301'));
    assert.equal(calls.length, 1);
    assert.equal(fs.existsSync(`${finalDir}.partial`), false);

    assert.equal(sbs.updateLauncher(layout, '0.3.301'), true, 'a newer launcher is copied to the root');
    assert.equal(fs.readFileSync(layout.launcherPath, 'utf8'), 'MZ launcher v2');
    assert.equal(sbs.updateLauncher(layout, '0.3.301'), false, 'an identical launcher is left alone');

    sbs.switchTo(layout, '0.3.301');
    assert.equal(sbs.readPointer(root, sbs.CURRENT_FILE), '0.3.301');
    assert.equal(sbs.readPointer(root, sbs.PREVIOUS_FILE), '0.3.300');
    assert.equal(sbs.pendingVersion(layout), '0.3.301');
    // The running version was never touched.
    assert.equal(fs.readFileSync(path.join(layout.appDir, sbs.EXE_NAME), 'utf8'), 'MZ app');

    // Prepared already: a second call reuses the folder.
    await sbs.prepareVersion(layout, {
      installerPath: 'x',
      version: '0.3.301',
      runPrepare: async () => assert.fail('must not unpack again'),
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('an incomplete or failed preparation leaves nothing to start', async () => {
  const { root, layout } = makeRoot();
  try {
    await assert.rejects(sbs.prepareVersion(layout, {
      installerPath: 'x',
      version: '0.3.301',
      runPrepare: async (_installer, target) => {
        fs.mkdirSync(target, { recursive: true });
        fs.writeFileSync(path.join(target, sbs.EXE_NAME), 'MZ');
      },
    }), /incomplete/);
    assert.equal(fs.existsSync(sbs.appDir(root, '0.3.301')), false);
    assert.equal(fs.existsSync(`${sbs.appDir(root, '0.3.301')}.partial`), false);

    await assert.rejects(sbs.prepareVersion(layout, {
      installerPath: 'x',
      version: '0.3.301',
      runPrepare: async () => { throw new Error('disk full'); },
    }), /disk full/);
    assert.equal(sbs.readPointer(root, sbs.CURRENT_FILE), '0.3.300', 'the pointer is untouched');
    assert.equal(sbs.pendingVersion(layout), '');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a version the launcher gave up on is not prepared again', async () => {
  const { root, layout } = makeRoot();
  try {
    makeApp(root, '0.3.301');
    fs.writeFileSync(path.join(sbs.appDir(root, '0.3.301'), sbs.BAD_FILE), 'did not start');
    await assert.rejects(sbs.prepareVersion(layout, {
      installerPath: 'x',
      version: '0.3.301',
      runPrepare: async () => assert.fail('must not run'),
    }), /did not start before/);
    assert.throws(() => sbs.switchTo(layout, '0.3.301'), /not ready/);
    await assert.rejects(sbs.prepareVersion(layout, {
      installerPath: 'x', version: '0.3.300', runPrepare: async () => {},
    }), /already running/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a good start is recorded and old versions are cleaned up', () => {
  const { root, layout } = makeRoot('0.3.301');
  try {
    makeApp(root, '0.3.299');
    makeApp(root, '0.3.300');
    fs.mkdirSync(path.join(root, 'app-0.3.302.partial'));
    fs.mkdirSync(path.join(root, 'app-0.3.302.partial.extract'));
    sbs.writePointer(root, sbs.PREVIOUS_FILE, '0.3.300');
    fs.writeFileSync(path.join(layout.appDir, sbs.ATTEMPTS_FILE), '1');

    sbs.markStartedOk(layout);
    assert.ok(fs.existsSync(path.join(layout.appDir, sbs.STARTED_OK_FILE)));
    assert.equal(fs.existsSync(path.join(layout.appDir, sbs.ATTEMPTS_FILE)), false);

    sbs.removePartials(root);
    assert.deepEqual(sbs.removeOldVersions(layout), ['0.3.299']);
    assert.deepEqual(sbs.installedVersions(root), ['0.3.300', '0.3.301']);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('after a rollback the settings from before the failed version come back', () => {
  const { root, layout } = makeRoot('0.3.300');
  const data = tempDir('dflash-sbs-data-');
  try {
    fs.writeFileSync(path.join(data, 'config.json'), '{"before":true}');
    fs.writeFileSync(path.join(data, 'auth.json'), '{"keys":1}');
    assert.equal(sbs.backupSettings(data, '0.3.301'), true);
    // 0.3.301 started, changed the config, and then failed.
    fs.writeFileSync(path.join(data, 'config.json'), '{"migrated":true}');
    fs.writeFileSync(path.join(root, sbs.ROLLED_BACK_FILE), '0.3.301');

    assert.equal(sbs.restoreAfterRollback(layout, data), '0.3.301');
    assert.equal(fs.readFileSync(path.join(data, 'config.json'), 'utf8'), '{"before":true}');
    assert.equal(fs.readFileSync(path.join(data, 'auth.json'), 'utf8'), '{"keys":1}');
    assert.equal(fs.existsSync(path.join(root, sbs.ROLLED_BACK_FILE)), false);
    assert.equal(sbs.restoreAfterRollback(layout, data), '', 'only once');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(data, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------- wiring (source)

test('the app wires prepare, restart, good-start and rollback in order', () => {
  const main = fs.readFileSync(path.join(repo, 'electron', 'main.js'), 'utf8');
  const check = main.slice(main.indexOf('async function checkAndDownloadUpdate()'));
  const stageAt = check.indexOf('await updateService.stageUpdate(manifest)');
  const prepareAt = check.indexOf('await prepareSideBySide(manifest)');
  const announceAt = check.indexOf('announceUpdateReady(manifest);\n}');
  assert.ok(stageAt > 0 && prepareAt > stageAt && announceAt > prepareAt,
    'download, then install beside, then tell the user');
  assert.match(main, /app\.relaunch\(\{ execPath: appLayout\.launcherPath, args: \['--dflash-post-update'\] \}\)/);
  assert.match(main, /relaunchPath: updateRelaunchPath\(\)/);
  assert.doesNotMatch(main, /relaunchPath: process\.execPath/, 'the setup must update the install root, not app-<version>');
  assert.match(main, /markThisVersionStarted\(\)/);
  assert.match(main, /relaunchCommand: `"\$\{appLayout\.launcherPath\}"`/);
  const ready = main.indexOf('app.whenReady().then(');
  assert.ok(main.indexOf('sideBySide.restoreAfterRollback', ready) > ready);
  assert.ok(
    main.indexOf('sideBySide.restoreAfterRollback', ready) < main.indexOf('void boot();', ready),
    'settings are restored before the backend starts',
  );
});

test('Start with Windows registers the launcher, not a versioned folder', () => {
  const settings = fs.readFileSync(path.join(repo, 'electron', 'app-settings.js'), 'utf8');
  const resolve = settings.slice(settings.indexOf('function resolveStartupExe()'));
  assert.ok(resolve.indexOf('DFLASH_LAUNCHER_PATH') < resolve.indexOf("app.getPath('exe')"));
});

// ------------------------------------------------- C# layout rules, launcher

function compile(outExe, sources, extraArgs = []) {
  execFileSync(csc, ['/nologo', '/target:exe', '/optimize+', `/out:${outExe}`, ...extraArgs, ...sources], {
    stdio: 'pipe',
  });
}

test('shared C# layout rules (launcher and setup)', windowsOnly, () => {
  const out = tempDir('dflash-layout-tests-');
  try {
    const exe = path.join(out, 'AppLayoutTests.exe');
    compile(exe, [
      path.join(repo, 'tests', 'csharp', 'AppLayoutTests.cs'),
      path.join(repo, 'tools', 'dflash-setup-ui', 'AppLayout.cs'),
    ]);
    const run = spawnSync(exe, [], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stdout + run.stderr);
    assert.match(run.stdout, /ALL PASSED/);
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }
});

let launcherExe = null;
function buildLauncher() {
  if (launcherExe) return launcherExe;
  const out = tempDir('dflash-launcher-build-');
  execFileSync('powershell.exe', [
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', path.join(repo, 'tools', 'dflash-launcher', 'build.ps1'), '-OutDir', out,
  ], { stdio: 'pipe' });
  launcherExe = path.join(out, sbs.EXE_NAME);
  return launcherExe;
}

function runLauncher(root, args) {
  const record = path.join(root, 'dry-run.txt');
  fs.rmSync(record, { force: true });
  const run = spawnSync(path.join(root, sbs.EXE_NAME), args, {
    env: { ...process.env, DFLASH_LAUNCHER_DRY_RUN: record },
    encoding: 'utf8',
  });
  const result = { status: run.status };
  if (fs.existsSync(record)) {
    for (const line of fs.readFileSync(record, 'utf8').split('\n')) {
      const at = line.indexOf('=');
      if (at > 0) result[line.slice(0, at)] = line.slice(at + 1);
    }
  }
  return result;
}

test('the real launcher starts current.txt and passes arguments through', windowsOnly, () => {
  const root = tempDir('dflash-launcher-');
  try {
    fs.copyFileSync(buildLauncher(), path.join(root, sbs.EXE_NAME));
    makeApp(root, '0.3.300');
    makeApp(root, '0.3.301');
    fs.writeFileSync(path.join(sbs.appDir(root, '0.3.300'), sbs.STARTED_OK_FILE), 'x');
    sbs.writePointer(root, sbs.PREVIOUS_FILE, '0.3.300');
    sbs.writePointer(root, sbs.CURRENT_FILE, '0.3.301');

    const first = runLauncher(root, ['--dflash-startup', 'a b', 'say "hi"', 'C:\\dir with space\\']);
    assert.equal(first.status, 0);
    assert.equal(first.exe, path.join(sbs.appDir(root, '0.3.301'), sbs.EXE_NAME));
    assert.equal(first.args, '--dflash-startup "a b" "say \\"hi\\"" "C:\\dir with space\\\\"');
    assert.equal(first.counted, '1', 'a new version is on probation');

    assert.equal(runLauncher(root, []).version, '0.3.301');
    const third = runLauncher(root, []);
    assert.equal(third.version, '0.3.300', 'two starts without started-ok: back to the previous version');
    assert.equal(third.rolledBackFrom, '0.3.301');
    assert.equal(sbs.readPointer(root, sbs.CURRENT_FILE), '0.3.300');
    assert.equal(fs.readFileSync(path.join(root, sbs.ROLLED_BACK_FILE), 'utf8').trim(), '0.3.301');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('the real launcher stops counting once the app marked a good start', windowsOnly, () => {
  const root = tempDir('dflash-launcher-');
  try {
    fs.copyFileSync(buildLauncher(), path.join(root, sbs.EXE_NAME));
    makeApp(root, '0.3.301');
    sbs.writePointer(root, sbs.CURRENT_FILE, '0.3.301');
    assert.equal(runLauncher(root, []).counted, '1');
    const layout = sbs.detectLayout(path.join(sbs.appDir(root, '0.3.301'), sbs.EXE_NAME));
    sbs.markStartedOk(layout);
    for (let i = 0; i < 3; i += 1) {
      const run = runLauncher(root, []);
      assert.equal(run.version, '0.3.301');
      assert.equal(run.counted, '0');
    }
    fs.rmSync(sbs.appDir(root, '0.3.301'), { recursive: true, force: true });
    assert.equal(runLauncher(root, []).status, 1, 'no app folder: the launcher reports it');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// ------------------------------------------------------ setup.exe /Prepare

function sevenZip() {
  return [
    path.join(process.env.ProgramFiles || 'C:\\Program Files', '7-Zip', '7z.exe'),
    path.join(repo, 'tools', '7za-x64.exe'),
  ].find((candidate) => fs.existsSync(candidate));
}

function buildFakeInstaller(dir, { complete = true } = {}) {
  const setupOut = path.join(dir, 'setup-build');
  execFileSync('powershell.exe', [
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', path.join(repo, 'tools', 'dflash-setup-ui', 'build.ps1'), '-OutDir', setupOut,
  ], { stdio: 'pipe' });
  const stage = path.join(dir, 'stage');
  makeApp(stage, 'x.y.z-not-used');
  const appStage = path.join(stage, 'app');
  fs.renameSync(sbs.appDir(stage, 'x.y.z-not-used'), appStage);
  if (!complete) fs.rmSync(path.join(appStage, 'resources', 'launcher'), { recursive: true, force: true });
  fs.writeFileSync(path.join(stage, 'install-version.txt'), '9.9.9');
  const archive = path.join(dir, 'payload.7z');
  execFileSync(sevenZip(), ['a', '-t7z', '-mx1', archive, path.join(stage, '*')], { stdio: 'pipe' });
  const installer = path.join(dir, 'DFlash-Console-Setup-9.9.9-x64.exe');
  const ui = fs.readFileSync(path.join(setupOut, 'dflash-setup-ui.exe'));
  const payload = fs.readFileSync(archive);
  const trailer = Buffer.alloc(24);
  trailer.write('DFLASHPL', 0, 'ascii');
  trailer.writeBigInt64LE(BigInt(ui.length), 8);
  trailer.writeBigInt64LE(BigInt(payload.length), 16);
  fs.writeFileSync(installer, Buffer.concat([ui, payload, trailer]));
  return installer;
}

const prepareSkip = csc && sevenZip() ? false : 'needs Windows, csc.exe and 7-Zip';

test('setup.exe /Prepare unpacks the app into app-<version>.partial without a window', { skip: prepareSkip, timeout: 180000 }, async () => {
  const dir = tempDir('dflash-prepare-');
  try {
    const installer = buildFakeInstaller(dir);
    const root = path.join(dir, 'install root');
    fs.mkdirSync(root);
    const target = path.join(root, 'app-9.9.9.partial');
    const result = await sbs.runSetupPrepare(installer, target, { timeoutMs: 120000 });
    assert.equal(result.ok, true);
    assert.ok(fs.existsSync(path.join(target, sbs.EXE_NAME)));
    assert.ok(fs.existsSync(path.join(target, 'resources', 'launcher', sbs.EXE_NAME)));
    assert.ok(fs.existsSync(path.join(target, 'install-version.txt')));
    assert.deepEqual(fs.readdirSync(root), ['app-9.9.9.partial'], 'no staging or result files left behind');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('setup.exe /Prepare rejects an incomplete package and leaves nothing', { skip: prepareSkip, timeout: 180000 }, async () => {
  const dir = tempDir('dflash-prepare-');
  try {
    const installer = buildFakeInstaller(dir, { complete: false });
    const root = path.join(dir, 'root');
    fs.mkdirSync(root);
    await assert.rejects(
      sbs.runSetupPrepare(installer, path.join(root, 'app-9.9.9.partial'), { timeoutMs: 120000 }),
      /incomplete/,
    );
    assert.deepEqual(fs.readdirSync(root), []);
    await assert.rejects(
      sbs.runSetupPrepare(installer, path.join(root, 'not-an-app-folder'), { timeoutMs: 120000 }),
      /app-<version>\.partial/,
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// -------------------------------------------------- setup install (source)

test('the setup installs into app-<version> and puts the launcher at the old exe path', () => {
  const setup = fs.readFileSync(path.join(repo, 'tools', 'dflash-setup-ui', 'SetupForm.cs'), 'utf8');
  const install = setup.slice(setup.indexOf('private async Task RunInstallAsync()'));
  const order = [
    'CopyProgramFiles(_payloadRoot, appDir)',
    'InstallLauncher(launcherSrc, launcherDest)',
    'AppLayout.RemoveFlatInstall(_destRoot, appDir)',
    'AppLayout.WritePointer(_destRoot, AppLayout.CurrentFile, _version)',
    'ApplyInstallPermissions(_destRoot, appDir, _perMachine)',
    'BootstrapDataRoot(appDir)',
    'CreateShortcuts(destExe)',
    'RegisterUninstall(destExe)',
  ].map((needle) => {
    const at = install.indexOf(needle);
    assert.ok(at > 0, `missing: ${needle}`);
    return at;
  });
  assert.deepEqual([...order].sort((a, b) => a - b), order, 'install steps run in order');
  assert.match(install, /string destExe = launcherDest;/, 'shortcuts and Apps & features name the launcher');
  assert.match(setup, /HasArg\(args, "\/Prepare"\)/);
  const build = fs.readFileSync(path.join(repo, 'tools', 'dflash-setup-ui', 'build.ps1'), 'utf8');
  assert.match(build, /AppLayout\.cs/);
  assert.match(build, /PrepareMode\.cs/);
  const installer = fs.readFileSync(path.join(repo, 'scripts', 'build-fast-installer.ps1'), 'utf8');
  assert.ok(installer.includes(String.raw`tools\dflash-launcher\build.ps1`));
  assert.ok(installer.includes(String.raw`resources\launcher`));
});

test('the header notice shows each stage and the right button', () => {
  const settings = fs.readFileSync(path.join(repo, 'static', 'js', 'app-settings-live.js'), 'utf8');
  const notice = settings.slice(settings.indexOf('function renderUpdateNotice('));
  assert.match(notice, /Downloading update · \$\{downloadPercent\(status\)\}%/);
  assert.match(notice, /preparing/);
  assert.match(notice, /'Restart to update' : 'Upgrade now'/);
  const popup = fs.readFileSync(path.join(repo, 'electron', 'update-popup.html'), 'utf8');
  assert.match(popup, /Restart to finish\. It takes a few seconds; loaded models reload afterwards\./);
  const index = fs.readFileSync(path.join(repo, 'static', 'index.html'), 'utf8');
  assert.match(index, /id="appUpdateFullSetupBtn"/);
});
