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

  // Keep an explicit quoted Run value as a compatibility fallback for older
  // Electron builds and repair entries written by earlier Console versions.
  try {
    const regQuiet = { windowsHide: true, stdio: 'ignore' };
    if (enabled) {
      const launchCommand = `"${exe}" ${STARTUP_ARGS.join(' ')}`;
      execFileSync(
        'reg',
        ['add', RUN_KEY, '/v', STARTUP_VALUE, '/t', 'REG_SZ', '/d', launchCommand, '/f'],
        regQuiet,
      );
      // A previous Task Manager disablement must not survive an explicit
      // checked setting. A missing approval value is treated as enabled.
      try {
        execFileSync('reg', ['delete', STARTUP_APPROVED_KEY, '/v', STARTUP_VALUE, '/f'], regQuiet);
      } catch (_err) {
        // The approval value is normally absent.
      }
      applied = true;
    } else {
      execFileSync('reg', ['delete', RUN_KEY, '/v', STARTUP_VALUE, '/f'], regQuiet);
      try {
        execFileSync('reg', ['delete', STARTUP_APPROVED_KEY, '/v', STARTUP_VALUE, '/f'], regQuiet);
      } catch (_err) {
        // The approval value is normally absent.
      }
      applied = true;
    }
  } catch (err) {
    errors.push(err);
  }

  if (!applied && errors.length) {
    console.error('Failed to update Windows startup registration:', errors[0]);
  }
  return applied;
}

function startupRegistrationState() {
  if (process.platform !== 'win32') return false;
  const exe = resolveStartupExe();
  if (!exe || !fs.existsSync(exe)) return false;
  try {
    const login = typeof app.getLoginItemSettings === 'function'
      ? app.getLoginItemSettings({ path: exe, args: STARTUP_ARGS })
      : null;
    if (login && Object.prototype.hasOwnProperty.call(login, 'executableWillLaunchAtLogin')) {
      return Boolean(login.executableWillLaunchAtLogin);
    }
    if (login && Object.prototype.hasOwnProperty.call(login, 'openAtLogin')) {
      return Boolean(login.openAtLogin);
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
