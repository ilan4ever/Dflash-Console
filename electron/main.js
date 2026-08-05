'use strict';

const { app, BrowserWindow, shell, dialog, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');

const DEFAULT_PORT = 8900;
const UI_HOST = '127.0.0.1';
const HEALTH_PATH = '/api/health';
const READY_TIMEOUT_MS = 90000;
const POLL_MS = 500;

let mainWindow = null;
let spawnedServer = null;
let startedByApp = false;
let activePort = DEFAULT_PORT;

function isConsoleRoot(candidate) {
  if (!candidate) return false;
  return (
    fs.existsSync(path.join(candidate, 'server.ps1')) &&
    fs.existsSync(path.join(candidate, 'api', 'app.py')) &&
    fs.existsSync(path.join(candidate, 'static', 'index.html'))
  );
}

function repoRoot() {
  const override = String(process.env.DFLASH_CONSOLE_ROOT || '').trim();
  if (override && isConsoleRoot(override)) {
    return path.resolve(override);
  }

  const candidates = [];
  if (app.isPackaged) {
    const nearExe = path.dirname(app.getPath('exe'));
    candidates.push(
      nearExe,
      path.resolve(nearExe, '..'),
      path.resolve(nearExe, '..', '..'),
      path.resolve(process.resourcesPath, '..'),
      process.resourcesPath,
    );
  } else {
    candidates.push(path.resolve(__dirname, '..'));
  }
  for (const candidate of candidates) {
    if (isConsoleRoot(candidate)) {
      return path.resolve(candidate);
    }
  }
  return null;
}

function configuredPort(root) {
  if (!root) return DEFAULT_PORT;
  try {
    const config = JSON.parse(fs.readFileSync(path.join(root, 'config.json'), 'utf8'));
    const port = Number(config && config.ui_port);
    if (Number.isInteger(port) && port >= 1 && port <= 65535) {
      return port;
    }
  } catch (_err) {
    // Fall back to the documented default when config is unavailable.
  }
  return DEFAULT_PORT;
}

async function chooseDataRoot() {
  const existing = repoRoot();
  if (existing) return existing;

  const result = await dialog.showOpenDialog({
    title: 'Choose DFlash Console data folder',
    message: 'Select the folder containing server.ps1, api, and static.',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths[0]) {
    throw new Error(
      'No Console data folder was selected. Set DFLASH_CONSOLE_ROOT and try again.',
    );
  }
  const selected = path.resolve(result.filePaths[0]);
  if (!isConsoleRoot(selected)) {
    throw new Error(
      'The selected folder is not a DFlash Console data root. It must contain server.ps1, api, and static.',
    );
  }
  process.env.DFLASH_CONSOLE_ROOT = selected;
  return selected;
}

function consoleUrl(port = DEFAULT_PORT) {
  return `http://${UI_HOST}:${port}/`;
}

function healthUrl(port = DEFAULT_PORT) {
  return `http://${UI_HOST}:${port}${HEALTH_PATH}`;
}

function iconPath() {
  const root = repoRoot();
  const candidates = [
    root ? path.join(root, 'assets', 'dflash_console_logo_only_clear.png') : null,
    path.join(__dirname, '..', 'assets', 'dflash_console_logo_only_clear.png'),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate)) || undefined;
}

function fetchHealth(port = DEFAULT_PORT) {
  return new Promise((resolve) => {
    const req = http.get(healthUrl(port), { timeout: 2000 }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        body += chunk;
      });
      res.on('end', () => {
        if (res.statusCode !== 200) {
          resolve(null);
          return;
        }
        try {
          const payload = JSON.parse(body);
          if (payload && payload.success === true && payload.app === 'DFlash Console') {
            resolve(payload);
            return;
          }
        } catch (_err) {
          // ignore parse errors
        }
        resolve(null);
      });
    });
    req.on('timeout', () => {
      req.destroy();
      resolve(null);
    });
    req.on('error', () => resolve(null));
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHealthy(port = DEFAULT_PORT, timeoutMs = READY_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const health = await fetchHealth(port);
    if (health) return health;
    await sleep(POLL_MS);
  }
  return null;
}

function findPwsh() {
  const candidates = [
    process.env.PWSH_PATH,
    'pwsh.exe',
    path.join(process.env.ProgramFiles || 'C:\\Program Files', 'PowerShell', '7', 'pwsh.exe'),
    path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'PowerShell', '7', 'pwsh.exe'),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      if (candidate === 'pwsh.exe' || fs.existsSync(candidate)) return candidate;
    } catch (_err) {
      // continue
    }
  }
  return 'pwsh.exe';
}

function startConsoleServer(port = DEFAULT_PORT) {
  const root = repoRoot();
  if (!root) {
    throw new Error('DFlash Console data root is not configured.');
  }
  const serverScript = path.join(root, 'server.ps1');
  if (!fs.existsSync(serverScript)) {
    throw new Error(`server.ps1 not found at ${serverScript}`);
  }
  const pwsh = findPwsh();
  const child = spawn(
    pwsh,
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', serverScript, '-Port', String(port)],
    {
      cwd: root,
      windowsHide: true,
      stdio: 'ignore',
      detached: false,
    },
  );
  spawnedServer = child;
  startedByApp = true;
  child.on('exit', () => {
    if (spawnedServer === child) spawnedServer = null;
  });
  return child;
}

async function ensureBackend() {
  let root = repoRoot();
  let port = configuredPort(root);
  let existing = await fetchHealth(port);
  if (existing) {
    activePort = port;
    startedByApp = false;
    return existing;
  }

  root = await chooseDataRoot();
  port = configuredPort(root);
  existing = await fetchHealth(port);
  if (existing) {
    activePort = port;
    startedByApp = false;
    return existing;
  }

  startConsoleServer(port);
  const health = await waitForHealthy(port);
  if (!health) {
    throw new Error(
      `DFlash Console API did not become ready on ${consoleUrl(port)}. Check logs\\startup.log.`,
    );
  }
  activePort = port;
  return health;
}

function buildMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Reload UI',
          accelerator: 'CmdOrCtrl+R',
          click: () => {
            if (mainWindow) mainWindow.reload();
          },
        },
        {
          label: 'Open in Browser',
          click: () => {
            void shell.openExternal(consoleUrl(activePort));
          },
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'togglefullscreen' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'toggleDevTools' },
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'API Health',
          click: async () => {
            const health = await fetchHealth(activePort);
            dialog.showMessageBox(mainWindow || undefined, {
              type: health ? 'info' : 'warning',
              title: 'DFlash Console',
              message: health
                ? `Online · v${health.version || '?'} · boot ${health.boot_id || '?'}`
                : 'Console API is offline.',
            });
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function createWindow() {
  const icon = iconPath();
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1100,
    minHeight: 720,
    backgroundColor: '#0b0f14',
    title: 'DFlash Console',
    autoHideMenuBar: true,
    show: false,
    icon,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once('ready-to-show', () => {
    if (mainWindow) mainWindow.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  await mainWindow.loadURL(consoleUrl(activePort));
}

async function boot() {
  buildMenu();
  try {
    await ensureBackend();
  } catch (err) {
    dialog.showErrorBox('DFlash Console', String(err && err.message ? err.message : err));
    app.quit();
    return;
  }
  await createWindow();
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(() => {
    void boot();
  });

  app.on('window-all-closed', () => {
    // Leave the local Console API and engines running, matching browser close behavior.
    app.quit();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void boot();
    }
  });
}
