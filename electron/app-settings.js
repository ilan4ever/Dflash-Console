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
  uiZoomFactor: 1,
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
  const startupRequested = Boolean(next.startWithWindows);
  const startupRegistered = applyWindowsStartup(startupRequested);
  if (startupRequested && !startupRegistered) {
    // Do not persist a checked box when Windows could not register the
    // executable. The UI will show the actual state on its next refresh.
    next.startWithWindows = false;
  }
  cached = next;
  fs.mkdirSync(app.getPath('userData'), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2), 'utf8');
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

const STARTUP_ARGS = ['--dflash-startup'];
const RUN_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';
const STARTUP_APPROVED_KEY =
  'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run';
const STARTUP_VALUE = 'DFlash Console';

function applyWindowsStartup(enabled) {
  if (process.platform !== 'win32') return true;
  // The developer copy (run from a checkout) shares the Windows app ID
  // com.dflash.console with the installed app. Its own "Start with Windows:
  // off" deleted the installed app's startup entry, so after the next restart
  // DFlash Console did not start (2026-11-26). Only the installed app manages
  // Windows startup; in the developer copy the setting stays off.
  if (!app.isPackaged) return !enabled;
  const exe = enabled ? resolveStartupExe() : (app.getPath('exe') || '');
  if (enabled && (!exe || !fs.existsSync(exe))) {
    console.error('Cannot enable Windows startup: installed executable was not found.');
    return false;
  }

  let applied = false;
  const errors = [];

  // Electron handles the Windows login-item bookkeeping and keeps
  // getLoginItemSettings() consistent with the Run entry.
  try {
    if (typeof app.setLoginItemSettings === 'function') {
      app.setLoginItemSettings({
        openAtLogin: enabled,
        path: exe,
        args: STARTUP_ARGS,
      });
      applied = true;
    }
  } catch (err) {
    errors.push(err);
  }

  // Electron's login item ("com.dflash.console") is the single startup entry.
  // Only write our own Run value when that API is unavailable; otherwise remove
  // the legacy "DFlash Console" value so the app is never registered twice.
  try {
    const regQuiet = { windowsHide: true, stdio: 'ignore' };
    const deleteLegacy = (key) => {
      try {
        execFileSync('reg', ['delete', key, '/v', STARTUP_VALUE, '/f'], regQuiet);
      } catch (_err) {
        // The legacy value is normally absent.
      }
    };
    if (enabled && !applied) {
      const launchCommand = `"${exe}" ${STARTUP_ARGS.join(' ')}`;
      execFileSync(
        'reg',
        ['add', RUN_KEY, '/v', STARTUP_VALUE, '/t', 'REG_SZ', '/d', launchCommand, '/f'],
        regQuiet,
      );
      // A previous Task Manager disablement must not survive an explicit
      // checked setting. A missing approval value is treated as enabled.
      deleteLegacy(STARTUP_APPROVED_KEY);
      applied = true;
    } else {
      deleteLegacy(RUN_KEY);
      deleteLegacy(STARTUP_APPROVED_KEY);
      if (!enabled) applied = true;
    }
  } catch (err) {
    errors.push(err);
  }

  if (!applied && errors.length) {
    console.error('Failed to update Windows startup registration:', errors[0]);
  }
  return applied;
}

// Electron names its Run value after the app's AppUserModelId.
const LOGIN_ITEM_NAME = 'com.dflash.console';

/**
 * True when Task Manager (Startup apps) has switched the entry off. Its
 * StartupApproved record is binary; an odd first byte means disabled, and a
 * missing record means enabled.
 */
function disabledInTaskManager(valueName) {
  try {
    const output = execFileSync(
      'reg',
      ['query', STARTUP_APPROVED_KEY, '/v', valueName],
      { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'], encoding: 'utf8' },
    );
    const match = String(output).match(/REG_BINARY\s+([0-9A-Fa-f]{2})/);
    return Boolean(match) && (parseInt(match[1], 16) % 2 === 1);
  } catch (_err) {
    return false;
  }
}

function startupRegistrationState() {
  if (process.platform !== 'win32') return false;
  const exe = resolveStartupExe();
  if (!exe || !fs.existsSync(exe)) return false;
  try {
    const login = typeof app.getLoginItemSettings === 'function'
      ? app.getLoginItemSettings({ path: exe, args: STARTUP_ARGS })
      : null;
    // executableWillLaunchAtLogin stays false until Task Manager has written a
    // StartupApproved record, which a fresh registration does not have, so
    // Settings said "needs attention" while startup worked (production test,
    // 2026-11-26). openAtLogin reads the Run entry; Task Manager can still
    // switch it off, so check that record separately.
    if (login && Object.prototype.hasOwnProperty.call(login, 'openAtLogin')) {
      return Boolean(login.openAtLogin) && !disabledInTaskManager(LOGIN_ITEM_NAME);
    }
  } catch (_err) {
    // Fall through to the registry probe for older Electron builds.
  }
  try {
    const output = execFileSync(
      'reg',
      ['query', RUN_KEY, '/v', STARTUP_VALUE],
      { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'], encoding: 'utf8' },
    );
    return String(output).toLowerCase().includes(String(exe).toLowerCase());
  } catch (_err) {
    return false;
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
  startupRegistrationState,
};
