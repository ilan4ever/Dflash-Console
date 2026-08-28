'use strict';

const { app } = require('electron');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const DEFAULTS = {
  minimizeToTray: true,
  startWithWindows: false,
  startMinimized: false,
  showSplashOnStartup: true,
  notifyOnEngineReady: false,
  allowAutomaticUpdates: true,
  postInstallWelcome: false,
  postInstallSetup: false,
};

let cached = null;

function settingsPath() {
  return path.join(app.getPath('userData'), 'app-settings.json');
}

function loadAppSettings() {
  if (cached) return { ...cached };
  try {
    const parsed = JSON.parse(fs.readFileSync(settingsPath(), 'utf8'));
    cached = { ...DEFAULTS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
  } catch (_err) {
    cached = { ...DEFAULTS };
  }
  return { ...cached };
}

function saveAppSettings(patch) {
  const current = loadAppSettings();
  const next = { ...current, ...(patch && typeof patch === 'object' ? patch : {}) };
  if (!next.minimizeToTray) {
    next.startMinimized = false;
  }
  cached = next;
  fs.mkdirSync(app.getPath('userData'), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2), 'utf8');
  applyWindowsStartup(Boolean(next.startWithWindows));
  return { ...next };
}

function resolveStartupExe() {
  // Never register a temporary/portable extraction path: the SFX update flow
  // runs the app from %TEMP%\7z... and registering that path makes Windows
  // launch a stale temp copy at every login (duplicate instances, repeated
  // update prompts after install). Prefer the real installed executable.
  const currentExe = app.getPath('exe') || '';
  const tempDirs = [process.env.TEMP, process.env.TMP]
    .map((dir) => String(dir || '').toLowerCase())
    .filter(Boolean);
  const isTempRun = tempDirs.some((dir) => currentExe.toLowerCase().startsWith(dir));
  if (!isTempRun) return currentExe;
  const installedCandidates = [
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'DFlash Console', 'DFlash Console.exe'),
    path.join(process.env.ProgramFiles || '', 'DFlash Console', 'DFlash Console.exe'),
  ];
  for (const candidate of installedCandidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  return '';
}

function applyWindowsStartup(enabled) {
  if (process.platform !== 'win32') return;
  const regKey = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';
  // reg.exe writes its error text straight to the shared console (it bypasses
  // the child's stdout/stderr pipes), so when the Run value is missing the
  // startup terminal would show "The system was unable to find the specified
  // registry key or value." Use stdio 'ignore' to keep app startup clean; exit
  // codes still propagate through the thrown Error.
  const regQuiet = { windowsHide: true, stdio: 'ignore' };
  try {
    if (enabled) {
      const exe = resolveStartupExe();
      if (!exe || !fs.existsSync(exe)) return; // nothing stable to register
      const launchCommand = `"${exe}" --dflash-startup`;
      execFileSync(
        'reg',
        ['add', regKey, '/v', 'DFlash Console', '/t', 'REG_SZ', '/d', launchCommand, '/f'],
        regQuiet,
      );
      return;
    }
    try {
      execFileSync('reg', ['delete', regKey, '/v', 'DFlash Console', '/f'], regQuiet);
    } catch (_err) {
      // Entry may already be absent.
    }
  } catch (err) {
    console.error('Failed to update Windows startup registration:', err);
  }
}

function syncStartupRegistration() {
  applyWindowsStartup(Boolean(loadAppSettings().startWithWindows));
}

module.exports = {
  DEFAULTS,
  loadAppSettings,
  saveAppSettings,
  syncStartupRegistration,
};
