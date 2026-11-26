'use strict';

// The developer copy must never change Windows startup. It shares the app ID
// com.dflash.console with the installed app, and its own "Start with Windows:
// off" deleted the installed app's startup entry (2026-11-26).

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');
const childProcess = require('child_process');

function loadAppSettingsWith(fakeApp, { regOutput = () => '' } = {}) {
  const calls = { loginItems: [], reg: [] };
  fakeApp.setLoginItemSettings = (options) => calls.loginItems.push(options);
  const originalLoad = Module._load;
  const originalExec = childProcess.execFileSync;
  // app-settings.js reads execFileSync when it loads, so swap it first.
  childProcess.execFileSync = (file, args) => {
    const line = [file, ...(args || [])].join(' ');
    calls.reg.push(line);
    return regOutput(line);
  };
  Module._load = function load(request, parent, isMain) {
    if (request === 'electron') return { app: fakeApp };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve('../electron/app-settings');
  delete require.cache[modulePath];
  try {
    return { settings: require(modulePath), calls };
  } finally {
    Module._load = originalLoad;
    childProcess.execFileSync = originalExec;
  }
}

test('the developer copy leaves Windows startup alone', { skip: process.platform !== 'win32' }, () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'dflash-startup-'));
  try {
    const fakeApp = {
      isPackaged: false,
      getPath: (name) => (name === 'userData' ? userData : 'C:\\dev\\node_modules\\electron\\dist\\electron.exe'),
    };
    const { settings, calls } = loadAppSettingsWith(fakeApp);

    settings.syncStartupRegistration();
    const off = settings.saveAppSettings({ startWithWindows: false });
    assert.equal(off.startWithWindows, false);
    const on = settings.saveAppSettings({ startWithWindows: true });
    assert.equal(on.startWithWindows, false, 'the box does not stay ticked when nothing was registered');

    assert.deepEqual(calls.loginItems, [], 'no Electron login item changes');
    assert.deepEqual(calls.reg.filter((line) => /\breg\b/i.test(line)), [], 'no Run key changes');
  } finally {
    fs.rmSync(userData, { recursive: true, force: true });
  }
});

test('Settings trusts the Run entry, unless Task Manager switched it off', { skip: process.platform !== 'win32' }, () => {
  // Electron's executableWillLaunchAtLogin stays false until Task Manager has
  // written a StartupApproved record, so a working entry read as "needs
  // attention" (production test, 2026-11-26).
  const exe = process.execPath;
  const fakeApp = {
    isPackaged: true,
    getPath: () => exe,
    getLoginItemSettings: () => ({ openAtLogin: true, executableWillLaunchAtLogin: false, launchItems: [] }),
  };
  let approved = '';
  const { settings } = loadAppSettingsWith(fakeApp, {
    regOutput: (line) => (line.includes('StartupApproved') ? approved : ''),
  });
  assert.equal(settings.startupRegistrationState(), true, 'no Task Manager record: active');
  approved = '    com.dflash.console    REG_BINARY    020000000000000000000000';
  assert.equal(settings.startupRegistrationState(), true, 'enabled in Task Manager');
  approved = '    com.dflash.console    REG_BINARY    030000006A1F7A6C2B5BDB01';
  assert.equal(settings.startupRegistrationState(), false, 'disabled in Task Manager');
  fakeApp.getLoginItemSettings = () => ({ openAtLogin: false, executableWillLaunchAtLogin: false });
  approved = '';
  assert.equal(settings.startupRegistrationState(), false, 'no Run entry');
});
