'use strict';

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const RUNTIME_ITEMS = [
  'api',
  'core',
  'static',
  'assets',
  'scripts',
  'runtime-bundles',
  'server.ps1',
  'run.ps1',
  'requirements.txt',
  'requirements.lock',
  'config.example.json',
];

function isConsoleRoot(candidate) {
  if (!candidate) return false;
  return (
    fs.existsSync(path.join(candidate, 'server.ps1'))
    && fs.existsSync(path.join(candidate, 'api', 'app.py'))
    && fs.existsSync(path.join(candidate, 'static', 'index.html'))
  );
}

function defaultUserDataRoot(homeDir) {
  return path.join(homeDir, 'DFlash Console');
}

function bundledRuntimePath(resourcesPath) {
  return path.join(resourcesPath, 'console-runtime');
}

function bundledRuntimeVersion(resourcesPath) {
  const stampPath = path.join(bundledRuntimePath(resourcesPath), '.runtime-version');
  if (!fs.existsSync(stampPath)) return '0';
  return String(fs.readFileSync(stampPath, 'utf8')).trim() || '0';
}

function copyDirRecursive(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const from = path.join(src, entry.name);
    const to = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(from, to);
    } else {
      fs.copyFileSync(from, to);
    }
  }
}

function writeFreshConfig(destRoot) {
  const examplePath = path.join(destRoot, 'config.example.json');
  const configPath = path.join(destRoot, 'config.json');
  let template = {};
  try {
    template = JSON.parse(fs.readFileSync(examplePath, 'utf8'));
  } catch (_err) {
    template = { ui_port: 8900, servers: [] };
  }

  const modelsDir = path.join(destRoot, 'models');
  const config = {
    ...template,
    ui_port: Number(template.ui_port) || 8900,
    dflash_root: destRoot,
    models_root: modelsDir,
    setup_complete: false,
    // Fresh installs should not inherit example engine profiles that reference
    // models missing on a new PC (avoids "target model path missing" errors).
    servers: [],
    model_libraries: [
      {
        id: 'dflash-checkpoints',
        label: 'DFlash Console models',
        path: modelsDir,
        enabled: true,
        preset: 'dflash',
        download_default: true,
      },
    ],
  };

  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, 'utf8');
}

function ensureRuntimeTree(destRoot, resourcesPath) {
  const src = bundledRuntimePath(resourcesPath);
  if (!fs.existsSync(src)) {
    throw new Error(`Console runtime bundle is missing at ${src}`);
  }

  const bundledVersion = bundledRuntimeVersion(resourcesPath);
  const stampPath = path.join(destRoot, '.runtime-version');
  const installedVersion = fs.existsSync(stampPath)
    ? String(fs.readFileSync(stampPath, 'utf8')).trim()
    : '';

  const runtimeComplete = RUNTIME_ITEMS.every((item) => fs.existsSync(path.join(destRoot, item)));
  const bundlesComplete = fs.existsSync(
    path.join(destRoot, 'runtime-bundles', 'transformers', 'server.py'),
  );
  if (isConsoleRoot(destRoot) && installedVersion === bundledVersion && runtimeComplete && bundlesComplete) {
    // A walk cut off before it finished runs again at the next start, not
    // only at the next runtime refresh.
    const script = path.join(destRoot, 'scripts', 'set-install-permissions.ps1');
    if (process.platform === 'win32' && fs.existsSync(script)) repairWholeDataRootOnce(destRoot, script);
    return destRoot;
  }

  fs.mkdirSync(destRoot, { recursive: true });
  for (const item of RUNTIME_ITEMS) {
    const from = path.join(src, item);
    const to = path.join(destRoot, item);
    if (!fs.existsSync(from)) continue;
    if (fs.statSync(from).isDirectory()) {
      if (fs.existsSync(to)) {
        fs.rmSync(to, { recursive: true, force: true });
      }
      copyDirRecursive(from, to);
    } else {
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.copyFileSync(from, to);
    }
  }

  for (const sub of ['models', 'logs', path.join('logs', 'presets')]) {
    fs.mkdirSync(path.join(destRoot, sub), { recursive: true });
  }

  if (!fs.existsSync(path.join(destRoot, 'config.json'))) {
    writeFreshConfig(destRoot);
  }

  fs.writeFileSync(stampPath, bundledVersion, 'utf8');
  ensureInstallPermissions(destRoot);
  return destRoot;
}

function ensureInstallPermissions(destRoot) {
  if (process.platform !== 'win32') return;
  const script = path.join(destRoot, 'scripts', 'set-install-permissions.ps1');
  if (!fs.existsSync(script)) return;
  // Only the runtime items this function just copied. The folder itself is
  // set too, and new files inherit from it. Walking the whole data root also
  // walked .venv and runtimes\ (tens of thousands of files), which hit the
  // 2-minute timeout on every first start of a new version.
  const result = spawnSync(
    'powershell.exe',
    [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
      '-Path', destRoot, '-Kind', 'data', '-Recurse', '-Only', RUNTIME_ITEMS.join(','),
    ],
    { windowsHide: true, timeout: 120000 },
  );
  if (result.status !== 0) {
    const detail = `${result.stderr || result.stdout || ''}`.trim();
    console.error(`Could not set Console folder access.${detail ? ` ${detail}` : ''}`);
  }
  repairWholeDataRootOnce(destRoot, script);
}

// Installs older than 0.3.298 can have a restricted .venv that only a walk of
// the whole data root repairs. Do that walk once, in the background so the
// app does not wait for it, and remember that it finished.
const FULL_ACCESS_STAMP = '.install-access-full';

// The walk took 104 s on OMAN, and the app wrote its "done" stamp only if it
// was still open when the walk ended (end-to-end test). The walk now writes
// the stamp itself when it succeeds, and is retried at each start
// until then. A walk started in the last 15 minutes is left to finish.
const FULL_ACCESS_RUNNING = '.install-access-full.running';
const FULL_ACCESS_RETRY_MS = 15 * 60 * 1000;

function psQuote(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function repairWholeDataRootOnce(destRoot, script) {
  const stamp = path.join(destRoot, FULL_ACCESS_STAMP);
  if (fs.existsSync(stamp)) return false;
  const running = path.join(destRoot, FULL_ACCESS_RUNNING);
  try {
    if (Date.now() - fs.statSync(running).mtimeMs < FULL_ACCESS_RETRY_MS) return false;
  } catch (_err) {
    // No walk is running.
  }
  try {
    fs.writeFileSync(running, `${new Date().toISOString()}\n`, 'utf8');
    // try/finally: a script that throws ends the whole command otherwise, and
    // the "running" marker would hold off the retry for 15 minutes.
    const command = '$ok = $false; '
      + `try { $LASTEXITCODE = 0; & ${psQuote(script)} -Path ${psQuote(destRoot)} -Kind data -Recurse -Force; `
      + '$ok = $? -and ($LASTEXITCODE -eq 0) } '
      + `finally { if ($ok) { Set-Content -LiteralPath ${psQuote(stamp)} -Value (Get-Date -Format o) }; `
      + `Remove-Item -LiteralPath ${psQuote(running)} -Force -ErrorAction SilentlyContinue }`;
    const child = spawn(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
      { windowsHide: true, stdio: 'ignore' },
    );
    child.on('error', () => {});
    child.unref();
    return true;
  } catch (_err) {
    // Best effort: the quick pass above already covered the new files.
    return false;
  }
}

function isDevCheckout(root) {
  if (!root) return false;
  return fs.existsSync(path.join(root, '.git'));
}

module.exports = {
  isConsoleRoot,
  isDevCheckout,
  defaultUserDataRoot,
  bundledRuntimePath,
  bundledRuntimeVersion,
  ensureRuntimeTree,
  repairWholeDataRootOnce,
};
