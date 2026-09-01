'use strict';

const { app, BrowserWindow, shell, dialog, Menu, Tray, nativeImage, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn, spawnSync } = require('child_process');
const {
  loadAppSettings,
  saveAppSettings,
  syncStartupRegistration,
} = require('./app-settings');
const { UpdateService } = require('./update-service');
const { compareVersions } = require('./update-contract');
const { registerContextMenus } = require('./context-menu');

const DEFAULT_PORT = 8900;
const UI_HOST = '127.0.0.1';
const HEALTH_PATH = '/api/health';
const READY_TIMEOUT_MS = 180000;
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
let updatePopupWindow = null;
let updatePromptDeferred = false;
let updatePopupShownFor = '';
let postInstallWelcomeActive = false;
let postInstallSetupActive = false;

const POST_INSTALL_LAUNCH_ARGS = new Set(['--dflash-post-update', '--dflash-post-install']);
const POST_INSTALL_SETUP_ARG = '--dflash-post-install';
const STARTUP_LAUNCH_ARG = '--dflash-startup';

function hasPostInstallLaunchArg() {
  return process.argv.some((arg) => POST_INSTALL_LAUNCH_ARGS.has(arg));
}

function hasPostInstallSetupArg() {
  return process.argv.includes(POST_INSTALL_SETUP_ARG);
}

function syncPostInstallWelcomeFlag() {
  if (hasPostInstallSetupArg()) {
    saveAppSettings({ postInstallSetup: true, postInstallWelcome: false });
  } else if (hasPostInstallLaunchArg()) {
    saveAppSettings({ postInstallWelcome: true, postInstallSetup: false });
  }
  const settings = loadAppSettings();
  postInstallWelcomeActive = Boolean(settings.postInstallWelcome);
  postInstallSetupActive = Boolean(settings.postInstallSetup);
}

function dismissPostInstallWelcome() {
  if (!postInstallWelcomeActive && !postInstallSetupActive) return;
  postInstallWelcomeActive = false;
  postInstallSetupActive = false;
  saveAppSettings({ postInstallWelcome: false, postInstallSetup: false });
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('post-install-welcome:cleared');
  }
}

function isStartupLaunch() {
  return process.argv.includes(STARTUP_LAUNCH_ARG);
}

function shouldShowMainWindowOnReady() {
  if (postInstallWelcomeActive || postInstallSetupActive) return true;
  // "Start minimized to tray" applies only when Windows launches the app at
  // sign-in (--dflash-startup). Opening from a desktop shortcut or tray always
  // shows the window, even when minimize-to-tray is enabled.
  if (isStartupLaunch() && loadAppSettings().startMinimized) return false;
  return true;
}

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
    // The installed (packaged) app is a SEPARATE application with its own data
    // root. A stale DFLASH_CONSOLE_ROOT that points at the developer git
    // checkout (inherited from a shell/profile) must not be honored here —
    // otherwise the installed app reuses the developer server and shows the
    // Developer badge inside the Electron app. Only non-checkout roots are
    // accepted as an override in packaged mode.
    if (!app.isPackaged || !isDevCheckout(override)) {
      const resolved = path.resolve(override);
      if (app.isPackaged) {
        ensureRuntimeTree(resolved, process.resourcesPath);
      }
      return resolved;
    }
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
  // The developer app never auto-updates — only the installed app does.
  if (!app.isPackaged) return null;
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
      if (updatePopupWindow && !updatePopupWindow.isDestroyed()) {
        updatePopupWindow.webContents.send('update:status', status);
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
  if (manifest) {
    // Ask first via an always-on-top popup: Install now, or Later (remind again
    // at the next app start). No silent background download.
    showUpdatePopup(manifest);
  }
}

function closeUpdatePopup() {
  if (updatePopupWindow && !updatePopupWindow.isDestroyed()) {
    updatePopupWindow.destroy();
  }
  updatePopupWindow = null;
}

function showUpdatePopup(manifest) {
  if (!manifest) return;
  if (updatePromptDeferred) return;
  const version = String(manifest.version || '').trim();
  if (!version || version === updatePopupShownFor) return;
  updatePopupShownFor = version;
  const notes = String(manifest.releaseNotes || '').trim();

  if (updatePopupWindow && !updatePopupWindow.isDestroyed()) {
    updatePopupWindow.webContents.send('update-popup:show', { version, notes });
    updatePopupWindow.show();
    updatePopupWindow.focus();
    return;
  }

  updatePopupWindow = new BrowserWindow({
    width: 500,
    height: 460,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: false,
    show: false,
    autoHideMenuBar: true,
    title: 'DFlash Console update',
    backgroundColor: '#0b0f14',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // Stay above normal windows (and most fullscreen apps) so the user sees it.
  updatePopupWindow.setAlwaysOnTop(true, 'screen-saver');
  updatePopupWindow.on('closed', () => {
    updatePopupWindow = null;
  });
  updatePopupWindow.loadFile(path.join(__dirname, 'update-popup.html'), {
    query: { version, notes },
  });
  updatePopupWindow.once('ready-to-show', () => {
    if (!updatePopupWindow || updatePopupWindow.isDestroyed()) return;
    updatePopupWindow.show();
    updatePopupWindow.focus();
  });
}

function registerUpdatePopupIpc() {
  ipcMain.handle('update-popup:install', async () => {
    if (!updateService) throw new Error('Automatic updates are not configured for this build.');
    const status = updateService.getStatus();
    let installerPath = status.ready && status.installerPath ? status.installerPath : null;
    if (!installerPath) {
      installerPath = await updateService.stageUpdate();
    }
    await updateService.launchInstaller(installerPath, {
      processId: process.pid,
      relaunchPath: process.execPath,
      relaunchArguments: [],
      onReady: async () => {
        setTimeout(() => app.quit(), 100);
      },
    });
    return updateService.getStatus();
  });

  ipcMain.handle('update-popup:later', () => {
    // In-memory only: the next popup appears on the next app start.
    updatePromptDeferred = true;
    closeUpdatePopup();
    return { deferred: true };
  });
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

function showMainWindow({ bringToFront = false } = {}) {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
  if (bringToFront) {
    mainWindow.setAlwaysOnTop(true);
    mainWindow.moveTop();
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.setAlwaysOnTop(false);
      }
    }, 300);
  }
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
    postInstallWelcome: postInstallWelcomeActive,
    postInstallSetup: postInstallSetupActive,
    isElectron: true,
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron,
    dataRoot: repoRoot(),
    consoleUrl: consoleUrl(activePort),
    userDataPath: app.getPath('userData'),
    platform: process.platform,
  }));

  ipcMain.handle('app-settings:set', (_event, patch) => {
    const saved = saveAppSettings(patch);
    if (patch && typeof patch === 'object') {
      if (Object.prototype.hasOwnProperty.call(patch, 'postInstallWelcome')) {
        postInstallWelcomeActive = Boolean(saved.postInstallWelcome);
      }
      if (Object.prototype.hasOwnProperty.call(patch, 'postInstallSetup')) {
        postInstallSetupActive = Boolean(saved.postInstallSetup);
      }
    }
    applyTrayFromSettings();
    return {
      ...saved,
      isElectron: true,
      appVersion: app.getVersion(),
      electronVersion: process.versions.electron,
      dataRoot: repoRoot(),
      consoleUrl: consoleUrl(activePort),
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
      consoleUrl: consoleUrl(activePort),
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
    tray.on('double-click', () => showMainWindow({ bringToFront: true }));
    tray.on('click', () => showMainWindow({ bringToFront: true }));
    tray.setContextMenu(
      Menu.buildFromTemplate([
        {
          label: 'Open DFlash Console',
          click: () => showMainWindow({ bringToFront: true }),
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
  setTimeout(() => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.setAlwaysOnTop(false);
    }
  }, 15000);
}

function closeSplashWindow() {
  if (!splashWindow) return;
  splashWindow.close();
  splashWindow = null;
}

function healthMatchesConsoleRoot(health, root) {
  if (!health || !root) return false;
  const resolved = path.resolve(root);
  const candidates = [health.process_root, health.console_root]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  if (!candidates.length) return !app.isPackaged;
  return candidates.some((candidate) => path.resolve(candidate) === resolved);
}

/**
 * A running server from an older/newer build must not be reused: its Python
 * code is frozen in memory, so the updated data root (new UI + new API) would
 * be served by stale backend code. Versions stay in sync across package.json
 * and core/version.py, so a mismatch always means "restart me".
 */
function healthMatchesAppVersion(health, root) {
  if (!health) return false;
  const appVersion = String(app.getVersion() || '').trim();
  if (!appVersion) return true;
  const serverVersion = String(health.version || '').trim();
  if (!serverVersion) return false;
  if (serverVersion === appVersion) return true;
  // The packaged app syncs newer Python/UI files into the data root on every
  // start. Reuse a same-root server that already runs that newer API instead
  // of stop/restart loops when the desktop shell exe was not updated yet.
  if (
    app.isPackaged
    && root
    && healthMatchesConsoleRoot(health, root)
    && compareVersions(serverVersion, appVersion) >= 0
  ) {
    return true;
  }
  return false;
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

function findPowerShellShell() {
  const programFiles = process.env.ProgramFiles || 'C:\\Program Files';
  const programFilesX86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)';
  const systemRoot = process.env.SystemRoot || 'C:\\Windows';
  const candidates = [
    process.env.PWSH_PATH,
    path.join(programFiles, 'PowerShell', '7', 'pwsh.exe'),
    path.join(programFilesX86, 'PowerShell', '7', 'pwsh.exe'),
    path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) return candidate;
    } catch (_err) {
      // continue
    }
  }
  try {
    const result = spawnSync('where.exe', ['pwsh.exe'], {
      windowsHide: true,
      encoding: 'utf8',
    });
    if (result.status === 0 && result.stdout) {
      const resolved = String(result.stdout).trim().split(/\r?\n/)[0].trim();
      if (resolved && fs.existsSync(resolved)) return resolved;
    }
  } catch (_err) {
    // fall through
  }
  const winPs = path.join(
    systemRoot,
    'System32',
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe',
  );
  if (fs.existsSync(winPs)) return winPs;
  throw new Error(
    'PowerShell was not found. Install PowerShell 7 or ensure Windows PowerShell is available.',
  );
}

function startConsoleServer(port = DEFAULT_PORT) {
  const root = repoRoot();
  if (!root) {
    throw new Error('DFlash Console data root is not configured.');
  }
  if (app.isPackaged) {
    // Repair a partially copied data root before Python resolves api.app.
    // This keeps the installed app independent of any developer checkout or
    // stale DFLASH_CONSOLE_ROOT value left in the user's environment.
    ensureRuntimeTree(root, process.resourcesPath);
  }
  const serverScript = path.join(root, 'server.ps1');
  if (!fs.existsSync(serverScript)) {
    throw new Error(`server.ps1 not found at ${serverScript}`);
  }
  const shellExe = findPowerShellShell();
  if (!fs.existsSync(shellExe)) {
    throw new Error(`PowerShell executable not found at ${shellExe}`);
  }
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
  // Foreground mode: the spawned shell runs uvicorn directly, so Electron owns
  // the live server process and can stop it (and its tree) when the app quits.
  const child = spawn(
    shellExe,
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', serverScript, '-Port', String(port), '-Foreground'],
    {
      cwd: root,
      windowsHide: true,
      stdio: ['ignore', outFd || 'ignore', errFd || 'ignore'],
      detached: false,
      env: {
        ...process.env,
        // Only the installed app marks its server as shell-owned. The developer
        // app leaves this unset so /api/health reports dev_server=true (git
        // checkout + no shell version) and the Developer badge shows.
        ...(app.isPackaged ? { DFLASH_CONSOLE_SHELL_VERSION: app.getVersion() } : {}),
        // App-owned servers release their managed engines on shutdown.
        DFLASH_CONSOLE_RELEASE_ON_SHUTDOWN: '1',
        // Lets the API exit itself if this shell process dies without a
        // graceful quit (force-kill, crash) so no stale server survives.
        DFLASH_CONSOLE_PARENT_PID: String(process.pid),
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
      const script = [
        'Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue',
        `| Where-Object { $_.LocalPort -eq ${Number(port)} -and $_.LocalAddress -in @('127.0.0.1','0.0.0.0','::','::1') }`,
        '| Select-Object -First 1 -ExpandProperty OwningProcess',
      ].join(' ');
      const child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], {
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore'],
      });
      let out = '';
      child.stdout.on('data', (chunk) => {
        out += chunk;
      });
      child.on('error', () => resolve(null));
      child.on('close', () => {
        const pid = String(out || '').trim().split(/\r?\n/).find((entry) => /^\d+$/.test(entry));
        resolve(pid ? Number(pid) : null);
      });
    } catch (_err) {
      resolve(null);
    }
  });
}

function raceTimeout(promise, ms) {
  return Promise.race([
    promise,
    sleep(ms).then(() => undefined),
  ]);
}

/**
 * Force-close the OTHER DFlash Console desktop shell (developer <-> installed).
 * Targets each foreign app's root process (parent is not another process with
 * the same image name). Installed main processes often have an empty WMI
 * CommandLine, so we cannot rely on --type= filtering alone.
 */
function closeOtherDesktopApp() {
  const selfPid = process.pid;
  const script = app.isPackaged
    ? `
$self = ${selfPid}
$targetName = 'electron.exe'
Get-CimInstance Win32_Process -Filter "Name='$targetName'" | ForEach-Object {
  if ($_.ProcessId -eq $self) { return }
  $cmd = [string]$_.CommandLine
  if ($cmd -and $cmd -match '--type=') { return }
  if ($cmd -and $cmd -notmatch '(?i)dflash-console') { return }
  $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
  if ($parent -and $parent.Name -eq $targetName) { return }
  & taskkill.exe /F /T /PID $_.ProcessId 2>$null | Out-Null
}
`
    : `
$self = ${selfPid}
$targetName = 'DFlash Console.exe'
Get-CimInstance Win32_Process -Filter "Name='$targetName'" | ForEach-Object {
  if ($_.ProcessId -eq $self) { return }
  $cmd = [string]$_.CommandLine
  if ($cmd -and $cmd -match '--type=') { return }
  $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
  if ($parent -and $parent.Name -eq $targetName) { return }
  & taskkill.exe /F /T /PID $_.ProcessId 2>$null | Out-Null
}
`;
  return new Promise((resolve) => {
    try {
      const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script], {
        windowsHide: true,
        stdio: 'ignore',
      });
      child.on('close', () => resolve());
      child.on('error', () => resolve());
    } catch (_err) {
      resolve();
    }
  });
}

/**
 * Close the other desktop shell and release any orphan API server it left on
 * our port. Never blocks startup indefinitely.
 */
async function closeOtherApp() {
  await raceTimeout(closeOtherDesktopApp(), 5000);
  try {
    const root = repoRoot();
    const port = configuredPort(root);
    const existing = await fetchHealth(port);
    if (!existing) return;
    if (healthMatchesConsoleRoot(existing, root) && healthMatchesAppVersion(existing, root)) {
      return;
    }
    await stopForeignConsole(port);
  } catch (_err) {
    // best effort — ensureBackend() still reconciles the port
  }
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
  if (existing && healthMatchesConsoleRoot(existing, root) && healthMatchesAppVersion(existing, root)) {
    activePort = port;
    startedByApp = false;
    return existing;
  }

  if (existing) {
    // A different Console instance (dev or installed) — or a stale server from
    // another version — holds the port. Stop it so only one current DFlash
    // server runs at a time on this PC, then take over.
    await stopForeignConsole(port);
  }

  root = root || await chooseDataRoot();
  port = configuredPort(root);
  const owned = await fetchHealth(port);
  if (owned && healthMatchesConsoleRoot(owned, root) && healthMatchesAppVersion(owned, root)) {
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
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'pasteAndMatchStyle' },
        { type: 'separator' },
        { role: 'selectAll' },
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
  const { screen } = require('electron');
  const workArea = screen.getPrimaryDisplay()?.workAreaSize || { width: 1440, height: 960 };
  const width = Math.min(1440, Math.max(960, workArea.width - 48));
  const height = Math.min(960, Math.max(640, workArea.height - 48));
  mainWindow = new BrowserWindow({
    width,
    height,
    minWidth: 820,
    minHeight: 560,
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
      // The window loads while hidden behind the splash. Default Chromium
      // throttling then delays the Engines poll until the page looks stuck.
      backgroundThrottling: false,
      // Keep localStorage/sessionStorage for http://127.0.0.1:<port>/ across restarts.
      partition: 'persist:dflash-console',
    },
  });

  mainWindow.once('ready-to-show', () => {
    closeSplashWindow();
    if (shouldShowMainWindowOnReady()) {
      showMainWindow({ bringToFront: !isStartupLaunch() });
    }
    flushPendingUpdatePrompt();
  });

  mainWindow.on('close', (event) => {
    if (isQuitting) return;
    if (postInstallWelcomeActive) {
      dismissPostInstallWelcome();
    }
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

  mainWindow.on('minimize', (event) => {
    if (postInstallWelcomeActive) {
      dismissPostInstallWelcome();
    }
    // "Minimize to system tray": hide into the tray instead of the taskbar.
    // The setting previously only handled the close (X) button, so clicking
    // the minimize button still landed in the taskbar.
    if (!loadAppSettings().minimizeToTray) return;
    if (!tray) ensureTray();
    if (!tray) return; // tray unavailable (e.g. no icon) — allow normal minimize
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

  try {
    await mainWindow.webContents.session.clearCache();
  } catch (_err) {
    // Cache clear is best-effort so a stale page cannot show old Settings.
  }
  const url = consoleUrl(activePort);
  mainWindow.setTitle(`DFlash Console — ${url}`);
  await mainWindow.loadURL(url);
}

async function focusOrBoot() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    showMainWindow({ bringToFront: true });
    return;
  }
  const deadline = Date.now() + 30000;
  while (booting && Date.now() < deadline) {
    await sleep(250);
    if (mainWindow && !mainWindow.isDestroyed()) {
      showMainWindow({ bringToFront: true });
      return;
    }
  }
  await boot();
  if (mainWindow && !mainWindow.isDestroyed()) {
    showMainWindow({ bringToFront: true });
  }
}

async function boot() {
  if (booting) return;
  booting = true;
  try {
    buildMenu();
    // Two separate apps (developer checkout and installed app): starting one
    // closes the other so only one desktop app runs at a time.
    await closeOtherApp();
    if (loadAppSettings().showSplashOnStartup) {
      createSplashWindow();
    }
    applyTrayFromSettings();
    await ensureBackend();
    closeSplashWindow();
    if (!mainWindow) {
      await createWindow();
    } else if (shouldShowMainWindowOnReady()) {
      showMainWindow({ bringToFront: !isStartupLaunch() });
    }
  } catch (err) {
    closeSplashWindow();
    dialog.showErrorBox('DFlash Console', String(err && err.message ? err.message : err));
    quitApp();
  } finally {
    booting = false;
  }
}

// The developer app is a SEPARATE application from the installed app. Give it
// its own userData folder so its single-instance lock, app settings and
// console-root persistence do not collide with the installed app (which uses
// %APPDATA%\DFlash Console). Electron derives the default userData from
// productName, so without this both apps would share one single-instance lock
// and the developer app would silently quit while the installed app runs.
if (!app.isPackaged) {
  app.setPath('userData', path.join(app.getPath('appData'), 'dflash-console'));
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.dflash.console');
  }

  app.on('second-instance', () => {
    void focusOrBoot();
  });

  app.whenReady().then(() => {
    registerContextMenus(app);
    syncPostInstallWelcomeFlag();
    updateService = createUpdateService();
    registerAppSettingsIpc();
    registerUpdatePopupIpc();
    syncStartupRegistration();
    if (updateService) {
      const autoCheckEnabled = () => loadAppSettings().allowAutomaticUpdates !== false;
      setTimeout(() => {
        if (autoCheckEnabled()) void checkAndDownloadUpdate().catch(() => {});
      }, 3000);
      // Check for updates every minute while the app runs (the check itself is
      // throttled to once per minute in checkAndDownloadUpdate).
      setInterval(() => {
        if (autoCheckEnabled()) void checkAndDownloadUpdate().catch(() => {});
      }, 60 * 1000);
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
      showMainWindow({ bringToFront: true });
      return;
    }
    if (BrowserWindow.getAllWindows().length === 0) {
      void boot();
    }
  });

  app.on('before-quit', (event) => {
    isQuitting = true;
    closeUpdatePopup();
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
