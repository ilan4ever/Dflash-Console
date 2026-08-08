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

function applyWindowsStartup(enabled) {
  if (process.platform !== 'win32') return;
  const exe = app.getPath('exe');
  const regKey = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';
  try {
    if (enabled) {
      execFileSync(
        'reg',
        ['add', regKey, '/v', 'DFlash Console', '/t', 'REG_SZ', '/d', exe, '/f'],
        { windowsHide: true },
      );
      return;
    }
    try {
      execFileSync('reg', ['delete', regKey, '/v', 'DFlash Console', '/f'], { windowsHide: true });
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
