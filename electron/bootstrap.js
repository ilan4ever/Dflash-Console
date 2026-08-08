'use strict';

const fs = require('fs');
const path = require('path');

const RUNTIME_ITEMS = [
  'api',
  'core',
  'static',
  'scripts',
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
  if (isConsoleRoot(destRoot) && installedVersion === bundledVersion && runtimeComplete) {
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
  return destRoot;
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
};
