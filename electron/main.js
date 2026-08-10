'use strict';

const { app, BrowserWindow, shell, dialog, Menu, Tray, nativeImage, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');
const {
  loadAppSettings,
  saveAppSettings,
  syncStartupRegistration,
} = require('./app-settings');
const { UpdateService } = require('./update-service');

const DEFAULT_PORT = 8900;
const UI_HOST = '127.0.0.1';
const HEALTH_PATH = '/api/health';
const READY_TIMEOUT_MS = 90000;
const POLL_MS = 500;

let mainWindow = null;
let splashWindow = null;
let tray = null;
let spawnedServer = null;
let startedByApp = false;
let activePort = DEFAULT_PORT;
let isQuitting = false;
let booting = false;
let updateService = null;
let lastAutomaticUpdateCheckAt = 0;

const {
  isConsoleRoot,
  isDevCheckout,
  defaultUserDataRoot,
  ensureRuntimeTree,
} = require('./bootstrap');

function rootConfigPath() {
  return path.join(app.getPath('userData'), 'console-root.json');
}

function readPersistedRoot() {
  try {
    const parsed = JSON.parse(fs.readFileSync(rootConfigPath(), 'utf8'));
    const root = String(parsed?.root || '').trim();
    return isConsoleRoot(root) ? path.resolve(root) : null;
  } catch (_err) {
    return null;
  }
}

function writePersistedRoot(root) {
  const resolved = path.resolve(root);
  fs.mkdirSync(app.getPath('userData'), { recursive: true });
  fs.writeFileSync(rootConfigPath(), JSON.stringify({ root: resolved }, null, 2), 'utf8');
  process.env.DFLASH_CONSOLE_ROOT = resolved;
}

function devRepoRoot() {
  if (app.isPackaged) return null;
  const adjacent = path.resolve(__dirname, '..');
  if (isConsoleRoot(adjacent)) return adjacent;
  return null;
}

function commonRootCandidates() {
  if (app.isPackaged) return [];
  const home = app.getPath('home');
  const names = ['Dflash-Console', 'DFlash-Console'];
  const roots = [
    path.join(home, 'dev', 'Dflash-Console'),
    path.join(home, 'Dflash-Console'),
    path.join(home, 'source', 'repos', 'Dflash-Console'),
    path.join(home, 'projects', 'Dflash-Console'),
  ];
  for (const name of names) {
    roots.push(path.join(home, 'dev', name), path.join(home, name));
  }
  const seen = new Set();
  return roots
    .map((candidate) => path.resolve(candidate))
    .filter((candidate) => {
      if (seen.has(candidate)) return false;
      seen.add(candidate);
      return isConsoleRoot(candidate);
    });
}

function packagedUserDataRoot() {
  const persisted = readPersistedRoot();
  if (persisted) return persisted;
  return path.resolve(defaultUserDataRoot(app.getPath('home')));
}

function ensurePackagedDataRoot() {
  const dest = packagedUserDataRoot();
  ensureRuntimeTree(dest, process.resourcesPath);
  writePersistedRoot(dest);
  return dest;
}

function repoRoot() {
  const devRoot = devRepoRoot();
  if (devRoot) {
    process.env.DFLASH_CONSOLE_ROOT = devRoot;
    return devRoot;
  }

  const override = String(process.env.DFLASH_CONSOLE_ROOT || '').trim();
  if (override && isConsoleRoot(override)) {
    return path.resolve(override);
  }

  if (app.isPackaged) {
    const dest = readPersistedRoot() || path.resolve(defaultUserDataRoot(app.getPath('home')));
    ensureRuntimeTree(dest, process.resourcesPath);
    writePersistedRoot(dest);
    process.env.DFLASH_CONSOLE_ROOT = dest;
    return dest;
  }

  const persisted = readPersistedRoot();
  if (persisted) {
    process.env.DFLASH_CONSOLE_ROOT = persisted;
    return persisted;
  }

  for (const candidate of commonRootCandidates()) {
    writePersistedRoot(candidate);
    return candidate;
  }

  const candidates = [];
  candidates.push(path.resolve(__dirname, '..'));
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
    message: app.isPackaged
      ? 'Select your Console data folder, or choose a git checkout of DFlash Console.'
      : 'Select the folder containing server.ps1, api, and static.',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths[0]) {
    if (app.isPackaged) {
      const fallback = ensurePackagedDataRoot();
      process.env.DFLASH_CONSOLE_ROOT = fallback;
      return fallback;
    }
    throw new Error(
      'No Console data folder was selected. Set DFLASH_CONSOLE_ROOT and try again.',
    );
  }
  const selected = path.resolve(result.filePaths[0]);
  if (!isConsoleRoot(selected)) {
    if (app.isPackaged) {
      try {
        ensureRuntimeTree(selected, process.resourcesPath);
      } catch (err) {
        throw new Error(
          'The selected folder is not a DFlash Console data root. It must contain server.ps1, api, and static.',
        );
      }
    } else {
      throw new Error(
        'The selected folder is not a DFlash Console data root. It must contain server.ps1, api, and static.',
      );
    }
  }
  process.env.DFLASH_CONSOLE_ROOT = selected;
  writePersistedRoot(selected);
  return selected;
}

function consoleUrl(port = DEFAULT_PORT) {
  return `http://${UI_HOST}:${port}/`;
}

function healthUrl(port = DEFAULT_PORT) {
  return `http://${UI_HOST}:${port}${HEALTH_PATH}`;
}

function readUpdateConfig() {
  const candidates = [
    path.join(__dirname, 'resources', 'update-endpoint.json'),
    path.join(process.resourcesPath || '', 'update-endpoint.json'),
  ];
  for (const candidate of candidates) {
    try {
      if (!fs.existsSync(candidate)) continue;
      const parsed = JSON.parse(fs.readFileSync(candidate, 'utf8'));
      if (parsed && typeof parsed === 'object') return parsed;
    } catch (_err) {
      // Fail closed if an optional configuration file is malformed.
    }
  }
  return {};
}

function readUpdatePublicKey() {
  const candidates = [
    path.join(__dirname, 'resources', 'update-manifest-public.pem'),
    path.join(process.resourcesPath || '', 'update-manifest-public.pem'),
  ];
  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) return fs.readFileSync(candidate, 'utf8');
    } catch (_err) {
      // Try the next packaged location.
    }
  }
  return '';
}

function createUpdateService() {
  const config = readUpdateConfig();
  const manifestUrl = String(
    process.env.DFLASH_UPDATE_MANIFEST_URL
      || config.manifestUrl
      || '',
  ).trim();
  const token = String(
    process.env.DFLASH_UPDATE_TOKEN
      || config.token
      || '',
  ).trim();
  if (!manifestUrl || !token) return null;
  return new UpdateService({
    manifestUrl,
    token,
    publicKey: readUpdatePublicKey(),
    helperPath: app.isPackaged
      ? path.join(process.resourcesPath, 'update-helper.ps1')
      : path.join(__dirname, 'update-helper.ps1'),
    currentVersion: app.getVersion(),
    onStatus: (status) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('update:status', status);
      }
    },
  });
}

function flushPendingUpdatePrompt() {
  if (updateService && loadAppSettings().allowAutomaticUpdates !== false) {
    void checkAndDownloadUpdate().catch(() => {});
  }
}

async function checkAndDownloadUpdate() {
  if (!updateService || loadAppSettings().allowAutomaticUpdates === false) return;
  const status = updateService.getStatus();
  if (status.ready) {
    return;
  }
  if (status.state === 'downloading' || status.state === 'installing') return;
  if (Date.now() - lastAutomaticUpdateCheckAt < 60 * 1000) return;
  lastAutomaticUpdateCheckAt = Date.now();
  const manifest = await updateService.checkForUpdate();
  if (manifest && !updateService.getStatus().ready) {
    await updateService.stageUpdate(manifest);
  }
}

function iconPath() {
  const roots = [
    repoRoot(),
    app.isPackaged ? app.getAppPath() : path.resolve(__dirname, '..'),
    path.dirname(app.getPath('exe')),
  ].filter(Boolean);
  const names = ['dflash_console_logo_only_clear.png', 'dflash_console_logo.png'];
  for (const root of roots) {
    for (const name of names) {
      const candidate = path.join(root, 'assets', name);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return undefined;
}

function trayIcon() {
  const iconFile = iconPath();
  if (!iconFile) return nativeImage.createEmpty();
  const image = nativeImage.createFromPath(iconFile);
  if (image.isEmpty()) return image;
  return image.resize({ width: 16, height: 16 });
}

function showMainWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function quitApp() {
  isQuitting = true;
  if (tray) {
    tray.destroy();
    tray = null;
  }
  app.quit();
}

function applyTrayFromSettings() {
  const settings = loadAppSettings();
  if (!settings.minimizeToTray) {
    if (tray) {
      tray.destroy();
      tray = null;
    }
    return;
  }
  ensureTray();
}

function registerAppSettingsIpc() {
  ipcMain.handle('app-settings:get', () => ({
    ...loadAppSettings(),
    isElectron: true,
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron,
    dataRoot: repoRoot(),
    userDataPath: app.getPath('userData'),
    platform: process.platform,
  }));

  ipcMain.handle('app-settings:set', (_event, patch) => {
    const saved = saveAppSettings(patch);
    applyTrayFromSettings();
    return {
      ...saved,
      isElectron: true,
      appVersion: app.getVersion(),
      electronVersion: process.versions.electron,
      dataRoot: repoRoot(),
      userDataPath: app.getPath('userData'),
      platform: process.platform,
    };
  });

  ipcMain.handle('app-settings:choose-data-root', async () => {
    const selected = await chooseDataRoot();
    return {
      ...loadAppSettings(),
      isElectron: true,
      appVersion: app.getVersion(),
      electronVersion: process.versions.electron,
      dataRoot: selected,
      userDataPath: app.getPath('userData'),
      platform: process.platform,
    };
  });

  ipcMain.handle('app-settings:open-user-data', () => {
    void shell.openPath(app.getPath('userData'));
  });

  ipcMain.handle('app:get-version', () => app.getVersion());

  ipcMain.handle('update:get-status', () => updateService?.getStatus() || {
    state: 'idle',
    configured: false,
    currentVersion: app.getVersion(),
    message: 'Automatic updates are not configured for this build.',
  });

  ipcMain.handle('update:check', async () => {
    if (!updateService) {
      return {
        state: 'idle',
        configured: false,
        currentVersion: app.getVersion(),
        message: 'Automatic updates are not configured for this build.',
      };
    }
    await updateService.checkForUpdate();
    return updateService.getStatus();
  });

  ipcMain.handle('update:download', async () => {
    if (!updateService) throw new Error('Automatic updates are not configured for this build.');
    const stagedPath = await updateService.stageUpdate();
    return { ...updateService.getStatus(), stagedPath, ready: true };
  });

  ipcMain.handle('update:install', async () => {
    if (!updateService) throw new Error('Automatic updates are not configured for this build.');
    const status = updateService.getStatus();
    if (!status.ready || !status.installerPath) {
      throw new Error('Download the update before installing it.');
    }
    await updateService.launchInstaller(status.installerPath, {
      processId: process.pid,
      relaunchPath: process.execPath,
      relaunchArguments: [],
      onReady: async () => {
        setTimeout(() => app.quit(), 100);
      },
    });
    return updateService.getStatus();
  });
}

function ensureTray() {
  if (tray) return;
  try {
    const icon = trayIcon();
    if (icon.isEmpty()) return;
    tray = new Tray(icon);
    tray.setToolTip('DFlash Console');
    tray.on('double-click', () => showMainWindow());
    tray.on('click', () => showMainWindow());
    tray.setContextMenu(
      Menu.buildFromTemplate([
        {
          label: 'Open DFlash Console',
          click: () => showMainWindow(),
        },
        { type: 'separator' },
        {
          label: 'Quit',
          click: () => quitApp(),
        },
      ]),
    );
  } catch (_err) {
    tray = null;
  }
}

function createSplashWindow() {
  if (!loadAppSettings().showSplashOnStartup) return;
  if (splashWindow) return;
  const icon = iconPath();
  splashWindow = new BrowserWindow({
    width: 440,
    height: 240,
    frame: false,
    resizable: false,
    center: true,
    show: true,
    alwaysOnTop: true,
    backgroundColor: '#0b0f14',
    icon,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  void splashWindow.loadFile(path.join(__dirname, 'splash.html'));
}

function closeSplashWindow() {
  if (!splashWindow) return;
  splashWindow.close();
  splashWindow = null;
}

function healthMatchesConsoleRoot(health, root) {
  if (!health || !root) return false;
  const reported = String(health.console_root || '').trim();
  if (!reported) return !app.isPackaged;
  return path.resolve(reported) === path.resolve(root);
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
  const logDir = path.join(root, 'logs');
  try {
    fs.mkdirSync(logDir, { recursive: true });
  } catch (_err) {
    // logs are best-effort
  }
  let outFd = null;
  let errFd = null;
  try {
    outFd = fs.openSync(path.join(logDir, 'console-server.log'), 'a');
    errFd = fs.openSync(path.join(logDir, 'console-server.err.log'), 'a');
  } catch (_err) {
    outFd = null;
    errFd = null;
  }
  // Foreground mode: the spawned pwsh runs uvicorn directly, so Electron owns
  // the live server process and can stop it (and its tree) when the app quits.
  const child = spawn(
    pwsh,
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', serverScript, '-Port', String(port), '-Foreground'],
    {
      cwd: root,
      windowsHide: true,
      stdio: ['ignore', outFd || 'ignore', errFd || 'ignore'],
      detached: false,
      env: {
        ...process.env,
        DFLASH_CONSOLE_SHELL_VERSION: app.getVersion(),
        // App-owned servers release their managed engines on shutdown.
        DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN: '1',
      },
    },
  );
  spawnedServer = child;
  startedByApp = true;
  child.on('exit', () => {
    if (spawnedServer === child) spawnedServer = null;
    try {
      if (outFd) fs.closeSync(outFd);
    } catch (_err) { /* ignore */ }
    try {
      if (errFd) fs.closeSync(errFd);
    } catch (_err) { /* ignore */ }
  });
  return child;
}

/**
 * Gracefully stop the server this app started (if any), waiting for it to
 * exit and force-killing the process tree as a fallback. Safe to call on quit.
 */
async function stopOwnedServer() {
  if (!startedByApp || !spawnedServer) return;
  const child = spawnedServer;
  const port = activePort;
  // 1) Graceful: ask the Console API to shut down (releases engines + gateway).
  try {
    const req = http.request(
      { host: UI_HOST, port, path: '/api/shutdown', method: 'POST', timeout: 3000 },
      (res) => {
        res.resume();
      },
    );
    req.on('timeout', () => req.destroy());
    req.on('error', () => {});
    req.end();
  } catch (_err) {
    // ignore network/transport errors; fall through to the kill fallback
  }
  // 2) Wait for the spawned process tree to exit on its own.
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    if (spawnedServer !== child || child.exitCode !== null) return;
    await sleep(200);
  }
  // 3) Fallback: force-kill the whole process tree.
  try {
    if (child.pid) {
      spawn('taskkill.exe', ['/F', '/T', '/PID', String(child.pid)], {
        windowsHide: true,
        stdio: 'ignore',
      });
    }
  } catch (_err) {
    // ignore
  }
}

function requestShutdown(port) {
  return new Promise((resolve) => {
    try {
      const req = http.request(
        { host: UI_HOST, port, path: '/api/shutdown', method: 'POST', timeout: 3000 },
        (res) => {
          res.resume();
          res.on('end', () => resolve(true));
        },
      );
      req.on('timeout', () => req.destroy());
      req.on('error', () => resolve(false));
      req.end();
    } catch (_err) {
      resolve(false);
    }
  });
}

function pidListeningOnPort(port) {
  return new Promise((resolve) => {
    try {
      const child = spawn('netstat.exe', ['-ano'], {
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore'],
      });
      let out = '';
      child.stdout.on('data', (chunk) => {
        out += chunk;
      });
      child.on('error', () => resolve(null));
      child.on('close', () => {
        const needle = `:${port}`;
        const line = String(out)
          .split(/\r?\n/)
          .find((entry) => entry.includes(needle) && entry.includes('LISTENING'));
        if (!line) {
          resolve(null);
          return;
        }
        const parts = line.trim().split(/\s+/);
        const pid = parts[parts.length - 1];
        resolve(pid && /^\d+$/.test(pid) ? Number(pid) : null);
      });
    } catch (_err) {
      resolve(null);
    }
  });
}

/**
 * Stop a foreign DFlash Console instance that holds the port, so only one
 * Console server runs at a time on this machine. Graceful /api/shutdown first
 * (releases engines + gateway), then force-kills whatever still listens.
 */
async function stopForeignConsole(port) {
  await requestShutdown(port);
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const pid = await pidListeningOnPort(port);
    if (!pid) return true;
    await sleep(300);
  }
  const pid = await pidListeningOnPort(port);
  if (pid) {
    spawn('taskkill.exe', ['/F', '/T', '/PID', String(pid)], {
      windowsHide: true,
      stdio: 'ignore',
    });
  }
  await sleep(1500);
  return !(await pidListeningOnPort(port));
}

async function ensureBackend() {
  let root = repoRoot();
  let port = configuredPort(root);
  const existing = await fetchHealth(port);
  if (existing && healthMatchesConsoleRoot(existing, root)) {
    activePort = port;
    startedByApp = false;
    return existing;
  }

  if (existing) {
    // A different Console instance (dev or installed) holds the port — stop it
    // so only one DFlash server runs at a time on this PC, then take over.
    await stopForeignConsole(port);
  }

  root = root || await chooseDataRoot();
  port = configuredPort(root);
  const owned = await fetchHealth(port);
  if (owned && healthMatchesConsoleRoot(owned, root)) {
    activePort = port;
    startedByApp = false;
    return owned;
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
        {
          label: 'Quit',
          click: () => quitApp(),
        },
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
      // Keep localStorage/sessionStorage for http://127.0.0.1:<port>/ across restarts.
      partition: 'persist:dflash-console',
    },
  });

  mainWindow.once('ready-to-show', () => {
    closeSplashWindow();
    if (!loadAppSettings().startMinimized) {
      showMainWindow();
    }
    flushPendingUpdatePrompt();
  });

  mainWindow.on('close', (event) => {
    if (isQuitting) return;
    if (!loadAppSettings().minimizeToTray) {
      isQuitting = true;
      if (tray) {
        tray.destroy();
        tray = null;
      }
      return;
    }
    event.preventDefault();
    mainWindow.hide();
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
  if (booting) return;
  booting = true;
  buildMenu();
  if (loadAppSettings().showSplashOnStartup) {
    createSplashWindow();
  }
  applyTrayFromSettings();
  try {
    await ensureBackend();
  } catch (err) {
    closeSplashWindow();
    dialog.showErrorBox('DFlash Console', String(err && err.message ? err.message : err));
    quitApp();
    return;
  } finally {
    booting = false;
  }
  if (!mainWindow) {
    await createWindow();
  } else if (!loadAppSettings().startMinimized) {
    closeSplashWindow();
    showMainWindow();
  } else {
    closeSplashWindow();
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.dflash.console');
  }

  app.on('second-instance', () => {
    if (mainWindow) {
      showMainWindow();
      return;
    }
    void boot();
  });

  app.whenReady().then(() => {
    updateService = createUpdateService();
    registerAppSettingsIpc();
    syncStartupRegistration();
    if (updateService) {
      const autoCheckEnabled = () => loadAppSettings().allowAutomaticUpdates !== false;
      setTimeout(() => {
        if (autoCheckEnabled()) void checkAndDownloadUpdate().catch(() => {});
      }, 3000);
      setInterval(() => {
        if (autoCheckEnabled()) void checkAndDownloadUpdate().catch(() => {});
      }, 5 * 60 * 1000);
      app.on('browser-window-focus', () => {
        if (autoCheckEnabled()) void checkAndDownloadUpdate().catch(() => {});
      });
    }
    void boot();
  });

  app.on('window-all-closed', () => {
    // Keep running in the tray on Windows after the main window is hidden.
  });

  app.on('activate', () => {
    if (mainWindow) {
      showMainWindow();
      return;
    }
    if (BrowserWindow.getAllWindows().length === 0) {
      void boot();
    }
  });

  app.on('before-quit', (event) => {
    isQuitting = true;
    if (startedByApp && spawnedServer) {
      // Own this server: wait for it to stop (graceful shutdown, then kill
      // fallback) before Electron fully exits so closing the app really
      // terminates the server it started.
      event.preventDefault();
      void (async () => {
        try {
          await stopOwnedServer();
        } catch (_err) {
          // ignore
        }
        setTimeout(() => app.exit(0), 100);
      })();
    }
  });
}
