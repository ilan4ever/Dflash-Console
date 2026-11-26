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

function loadAppSettingsWith(fakeApp) {
  const calls = { loginItems: [], reg: [] };
  fakeApp.setLoginItemSettings = (options) => calls.loginItems.push(options);
  const originalLoad = Module._load;
  const originalExec = childProcess.execFileSync;
  // app-settings.js reads execFileSync when it loads, so swap it first.
  childProcess.execFileSync = (file, args) => {
    calls.reg.push([file, ...(args || [])].join(' '));
    return '';
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
