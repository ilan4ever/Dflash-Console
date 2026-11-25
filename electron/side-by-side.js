'use strict';

// Side-by-side program layout, seen from the running app. The setup UI and
// the launcher use the same files (tools/dflash-setup-ui/AppLayout.cs); see
// docs/plans/side-by-side-updates.md.
//
//   <root>\DFlash Console.exe        launcher
//   <root>\current.txt / previous.txt
//   <root>\rolled-back.txt           version the launcher abandoned
//   <root>\app-<version>\            one complete app per version
//       started-ok / launch-attempts / bad
//   <root>\app-<version>.partial\    being prepared

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { compareVersions } = require('./update-contract');

const EXE_NAME = 'DFlash Console.exe';
const CURRENT_FILE = 'current.txt';
const PREVIOUS_FILE = 'previous.txt';
const ROLLED_BACK_FILE = 'rolled-back.txt';
const STARTED_OK_FILE = 'started-ok';
const ATTEMPTS_FILE = 'launch-attempts';
const BAD_FILE = 'bad';
const APP_DIR_PREFIX = 'app-';
const PARTIAL_SUFFIX = '.partial';
const VERSION_PATTERN = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;
const SETTINGS_TO_BACK_UP = ['config.json', 'auth.json'];

function isValidVersion(value) {
  return VERSION_PATTERN.test(String(value || ''));
}

function appDir(root, version) {
  return path.join(root, `${APP_DIR_PREFIX}${version}`);
}

/**
 * The layout this app runs from, or null for the old flat install, a portable
 * copy or the developer checkout.
 */
function detectLayout(execPath, fsImpl = fs) {
  const dir = path.dirname(String(execPath || ''));
  const name = path.basename(dir);
  if (!name.startsWith(APP_DIR_PREFIX)) return null;
  const version = name.slice(APP_DIR_PREFIX.length);
  if (!isValidVersion(version)) return null;
  const root = path.dirname(dir);
  const launcherPath = path.join(root, EXE_NAME);
  if (!fsImpl.existsSync(launcherPath)) return null;
  return { root, appDir: dir, version, launcherPath };
}

function readPointer(root, name) {
  try {
    const value = fs.readFileSync(path.join(root, name), 'utf8').trim();
    return isValidVersion(value) ? value : '';
  } catch (_err) {
    return '';
  }
}

/** Write a temporary file and rename it over the old one: never half-written. */
function writeTextAtomic(filePath, text) {
  const temp = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(temp, text, 'utf8');
  try {
    fs.renameSync(temp, filePath);
  } finally {
    fs.rmSync(temp, { force: true });
  }
}

function writePointer(root, name, version) {
  if (!isValidVersion(version)) throw new Error(`Not a version: ${version}`);
  writeTextAtomic(path.join(root, name), version);
}

/** Program Files needs an administrator: those installs keep the setup window. */
function canPrepareInPlace(layout) {
  if (!layout) return false;
  const probe = path.join(layout.root, `.dflash-write-test-${process.pid}`);
  try {
    fs.writeFileSync(probe, 'ok');
    fs.rmSync(probe, { force: true });
    return true;
  } catch (_err) {
    return false;
  }
}

function isUsable(root, version) {
  if (!isValidVersion(version)) return false;
  const dir = appDir(root, version);
  return fs.existsSync(path.join(dir, EXE_NAME)) && !fs.existsSync(path.join(dir, BAD_FILE));
}

function installedVersions(root) {
  let entries = [];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch (_err) {
    return [];
  }
  return entries
    .filter((entry) => entry.isDirectory() && entry.name.startsWith(APP_DIR_PREFIX))
    .map((entry) => entry.name.slice(APP_DIR_PREFIX.length))
    .filter(isValidVersion)
    .sort(compareVersions);
}

/** Folders left by a preparation that was cut off. */
function removePartials(root) {
  let entries = [];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch (_err) {
    return;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.startsWith(APP_DIR_PREFIX)) continue;
    if (!entry.name.includes(PARTIAL_SUFFIX)) continue;
    try {
      fs.rmSync(path.join(root, entry.name), { recursive: true, force: true });
    } catch (_err) {
      // Retried at the next start.
    }
  }
}

/**
 * Unpack the verified installer into app-<version> next to the running app.
 * runPrepare(installerPath, targetDir) does the unpacking (setup.exe /Prepare).
 * Returns the final folder. The running version's folder is never touched.
 */
async function prepareVersion(layout, { installerPath, version, runPrepare }) {
  if (!layout) throw new Error('This install does not use side-by-side folders.');
  if (!isValidVersion(version)) throw new Error(`Not a version: ${version}`);
  if (version === layout.version) throw new Error(`Version ${version} is already running.`);
  const finalDir = appDir(layout.root, version);
  if (fs.existsSync(path.join(finalDir, BAD_FILE))) {
    throw new Error(`Version ${version} did not start before; use the full setup.`);
  }
  if (fs.existsSync(path.join(finalDir, EXE_NAME))) return finalDir;

  const partial = `${finalDir}${PARTIAL_SUFFIX}`;
  fs.rmSync(partial, { recursive: true, force: true });
  fs.rmSync(finalDir, { recursive: true, force: true });
  await runPrepare(installerPath, partial);
  for (const required of [
    EXE_NAME,
    path.join('resources', 'launcher', EXE_NAME),
    path.join('resources', 'console-runtime', 'server.ps1'),
  ]) {
    if (!fs.existsSync(path.join(partial, required))) {
      fs.rmSync(partial, { recursive: true, force: true });
      throw new Error(`The prepared version is incomplete (${required} missing).`);
    }
  }
  fs.renameSync(partial, finalDir);
  return finalDir;
}

/** Start setup.exe /Prepare and wait for its result file. */
function runSetupPrepare(installerPath, targetDir, { timeoutMs = 15 * 60 * 1000 } = {}) {
  const resultPath = `${targetDir}.result.json`;
  fs.rmSync(resultPath, { force: true });
  return new Promise((resolve, reject) => {
    const child = spawn(installerPath, ['/Prepare', `/Target=${targetDir}`, `/Result=${resultPath}`], {
      windowsHide: true,
      stdio: 'ignore',
    });
    const timer = setTimeout(() => {
      try { child.kill(); } catch (_err) { /* already gone */ }
      reject(new Error('Preparing the update took too long.'));
    }, timeoutMs);
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      let result = null;
      try {
        result = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
      } catch (_err) {
        result = null;
      }
      fs.rmSync(resultPath, { force: true });
      if (code === 0 && result?.ok) resolve(result);
      else reject(new Error(result?.error || `Preparing the update failed (code ${code}).`));
    });
  });
}

/** A newer launcher ships inside each app folder; copy it to the root when it differs. */
function updateLauncher(layout, version) {
  const source = path.join(appDir(layout.root, version), 'resources', 'launcher', EXE_NAME);
  if (!fs.existsSync(source)) return false;
  try {
    const next = fs.readFileSync(source);
    const current = fs.existsSync(layout.launcherPath) ? fs.readFileSync(layout.launcherPath) : null;
    if (current && current.equals(next)) return false;
    const temp = `${layout.launcherPath}.new`;
    fs.writeFileSync(temp, next);
    fs.renameSync(temp, layout.launcherPath);
    return true;
  } catch (_err) {
    // The launcher may be running for a moment; the next update retries.
    return false;
  }
}

/** Point the launcher at the new version; the running one becomes the way back. */
function switchTo(layout, version) {
  if (!isUsable(layout.root, version)) throw new Error(`Version ${version} is not ready.`);
  for (const name of [STARTED_OK_FILE, ATTEMPTS_FILE]) {
    fs.rmSync(path.join(appDir(layout.root, version), name), { force: true });
  }
  writePointer(layout.root, PREVIOUS_FILE, layout.version);
  writePointer(layout.root, CURRENT_FILE, version);
}

/** The pointer already names a version newer than the one running. */
function pendingVersion(layout) {
  if (!layout) return '';
  const current = readPointer(layout.root, CURRENT_FILE);
  if (!current || current === layout.version) return '';
  return isUsable(layout.root, current) ? current : '';
}

/** Tell the launcher this version really started; it stops counting attempts. */
function markStartedOk(layout) {
  if (!layout) return;
  fs.writeFileSync(path.join(layout.appDir, STARTED_OK_FILE), new Date().toISOString(), 'utf8');
  fs.rmSync(path.join(layout.appDir, ATTEMPTS_FILE), { force: true });
}

/** Keep current, previous and the running version; drop older folders. */
function removeOldVersions(layout) {
  if (!layout) return [];
  const keep = new Set([
    layout.version,
    readPointer(layout.root, CURRENT_FILE),
    readPointer(layout.root, PREVIOUS_FILE),
  ].filter(Boolean));
  const removed = [];
  for (const version of installedVersions(layout.root)) {
    if (keep.has(version)) continue;
    try {
      fs.rmSync(appDir(layout.root, version), { recursive: true, force: true });
      removed.push(version);
    } catch (_err) {
      // In use: retried at the next start.
    }
  }
  return removed;
}

function backupDir(dataRoot, version) {
  return path.join(dataRoot, 'backups', `pre-${version}`);
}

/** Copy the settings a newer version might change, before it first starts. */
function backupSettings(dataRoot, version) {
  if (!dataRoot || !isValidVersion(version)) return false;
  const dest = backupDir(dataRoot, version);
  fs.mkdirSync(dest, { recursive: true });
  for (const name of SETTINGS_TO_BACK_UP) {
    const source = path.join(dataRoot, name);
    if (fs.existsSync(source)) fs.copyFileSync(source, path.join(dest, name));
  }
  return true;
}

/**
 * After the launcher went back from a version that would not start, put the
 * settings saved before that version back. Returns the abandoned version.
 */
function restoreAfterRollback(layout, dataRoot) {
  if (!layout) return '';
  const marker = path.join(layout.root, ROLLED_BACK_FILE);
  let from = '';
  try {
    from = fs.readFileSync(marker, 'utf8').trim();
  } catch (_err) {
    return '';
  }
  if (isValidVersion(from) && dataRoot) {
    const source = backupDir(dataRoot, from);
    for (const name of SETTINGS_TO_BACK_UP) {
      const saved = path.join(source, name);
      if (fs.existsSync(saved)) fs.copyFileSync(saved, path.join(dataRoot, name));
    }
  }
  fs.rmSync(marker, { force: true });
  return isValidVersion(from) ? from : '';
}

module.exports = {
  EXE_NAME,
  CURRENT_FILE,
  PREVIOUS_FILE,
  ROLLED_BACK_FILE,
  STARTED_OK_FILE,
  ATTEMPTS_FILE,
  BAD_FILE,
  appDir,
  backupSettings,
  canPrepareInPlace,
  detectLayout,
  installedVersions,
  isUsable,
  isValidVersion,
  markStartedOk,
  pendingVersion,
  prepareVersion,
  readPointer,
  removeOldVersions,
  removePartials,
  restoreAfterRollback,
  runSetupPrepare,
  switchTo,
  updateLauncher,
  writePointer,
};
