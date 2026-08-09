'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const {
  DFLASH_SETUP_FILE,
  assertValidManifest,
  compareVersions,
  normalizeUpdateConfig,
} = require('./update-contract');
const { verifyFile, verifyManifestSignature } = require('./update-integrity');

const STATUS = Object.freeze({
  CHECKING: 'checking',
  AVAILABLE: 'available',
  DOWNLOADING: 'downloading',
  READY: 'ready',
  INSTALLING: 'installing',
  UP_TO_DATE: 'up-to-date',
  ERROR: 'error',
});

function safeArtifactName(fileName) {
  return DFLASH_SETUP_FILE.test(fileName) && path.basename(fileName) === fileName;
}

function quoteCmdArg(value) {
  const text = String(value);
  if (!/[ \t"]/g.test(text)) return text;
  return `"${text.replace(/"/g, '""')}"`;
}

async function launchDetachedUpdateHelper(helperPath, args, launcherPath) {
  const batPath = launcherPath.replace(/\.ps1$/i, '.cmd');
  const psTail = args.flatMap(([flag, value]) => [flag, value]).map(quoteCmdArg).join(' ');
  const bat = `@echo off\r\nstart "" /b powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ${quoteCmdArg(helperPath)} ${psTail}\r\n`;
  await fs.promises.writeFile(batPath, bat, 'utf8');
  await new Promise((resolve, reject) => {
    const child = spawn('cmd.exe', ['/d', '/c', batPath], { windowsHide: true, stdio: 'ignore' });
    child.once('error', reject);
    child.once('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`DFlash update launcher failed with exit code ${code}`));
    });
  });
}

class UpdateService {
  constructor(options = {}) {
    this.config = normalizeUpdateConfig(options);
    this.currentVersion = options.currentVersion || null;
    this.fetch = options.fetch || globalThis.fetch;
    this.onStatus = typeof options.onStatus === 'function' ? options.onStatus : () => {};
    this.helperPath = options.helperPath || path.join(__dirname, 'update-helper.ps1');
    this.stagingDir = this.config.stagingDir || path.join(os.tmpdir(), 'DFlash-Console-updates');
    this.manifest = null;
    this.statusValue = {
      state: 'idle',
      currentVersion: this.currentVersion || '',
      latestVersion: '',
      updateAvailable: false,
      percentage: 0,
      message: '',
      error: '',
      releaseNotes: '',
      installerPath: '',
      ready: false,
    };
  }

  status(state, details = {}) {
    this.statusValue = {
      ...this.statusValue,
      state,
      ...details,
    };
    this.onStatus({ ...this.statusValue });
  }

  getStatus() {
    return { ...this.statusValue };
  }

  authorizedUrl(rawUrl) {
    const url = new URL(rawUrl);
    if (this.config.token && !url.searchParams.has('token')) {
      url.searchParams.set('token', this.config.token);
    }
    return url.toString();
  }

  async fetchManifest() {
    this.status(STATUS.CHECKING);
    const response = await this.fetch(this.authorizedUrl(this.config.manifestUrl), {
      headers: { Accept: 'application/json', Authorization: `Bearer ${this.config.token}` },
    });
    if (!response.ok) throw new Error(`Update manifest request failed (${response.status})`);
    const manifest = await response.json();
    assertValidManifest(manifest, this.config);
    verifyManifestSignature(manifest, this.config.publicKey);
    return manifest;
  }

  async checkForUpdate() {
    try {
      const manifest = await this.fetchManifest();
      const available = !this.currentVersion || compareVersions(manifest.version, this.currentVersion) > 0;
      this.manifest = available ? manifest : null;
      this.status(available ? STATUS.AVAILABLE : STATUS.UP_TO_DATE, {
        manifest: available ? manifest : null,
        latestVersion: manifest.version,
        updateAvailable: available,
        releaseNotes: manifest.releaseNotes,
        message: available ? `DFlash Console ${manifest.version} is available.` : 'DFlash Console is up to date.',
        error: '',
      });
      return available ? manifest : null;
    } catch (error) {
      this.status(STATUS.ERROR, {
        error: error?.message || String(error),
        message: error?.message || 'Update check failed.',
      });
      throw error;
    }
  }

  async download(manifest) {
    assertValidManifest(manifest, this.config);
    if (!safeArtifactName(manifest.fileName)) throw new Error('Unsafe DFlash update artifact name');
    await fs.promises.mkdir(this.stagingDir, { recursive: true });
    const tempPath = path.join(this.stagingDir, `.${manifest.fileName}.part`);
    const stagedPath = path.join(this.stagingDir, manifest.fileName);
    const response = await this.fetch(this.authorizedUrl(manifest.downloadUrl), {
      headers: { Accept: 'application/octet-stream', Authorization: `Bearer ${this.config.token}` },
    });
    if (!response.ok || !response.body) throw new Error(`Update download failed (${response.status})`);
    this.status(STATUS.DOWNLOADING, { manifest, receivedBytes: 0, totalBytes: manifest.sizeBytes });
    const file = fs.createWriteStream(tempPath, { flags: 'wx' });
    let receivedBytes = 0;
    try {
      for await (const chunk of response.body) {
        receivedBytes += chunk.length;
        if (receivedBytes > manifest.sizeBytes) throw new Error('Update download exceeds manifest size');
        if (!file.write(chunk)) await new Promise((resolve) => file.once('drain', resolve));
        this.status(STATUS.DOWNLOADING, {
          manifest,
          receivedBytes,
          totalBytes: manifest.sizeBytes,
          percentage: Math.round((receivedBytes / manifest.sizeBytes) * 100),
        });
      }
      await new Promise((resolve, reject) => file.end((error) => error ? reject(error) : resolve()));
      await verifyFile(tempPath, manifest.sha512, manifest.sizeBytes);
      await fs.promises.rm(stagedPath, { force: true });
      await fs.promises.rename(tempPath, stagedPath);
      this.status(STATUS.READY, {
        manifest,
        stagedPath,
        installerPath: stagedPath,
        ready: true,
        percentage: 100,
        message: `DFlash Console ${manifest.version} is ready to install.`,
      });
      return stagedPath;
    } catch (error) {
      file.destroy();
      await fs.promises.rm(tempPath, { force: true });
      this.status(STATUS.ERROR, {
        error: error?.message || String(error),
        message: error?.message || 'Update download failed.',
        manifest,
      });
      throw error;
    }
  }

  async resumeStagedUpdate(manifest) {
    const target = manifest || this.manifest;
    if (!target || !safeArtifactName(target.fileName)) return null;
    const stagedPath = path.join(this.stagingDir, target.fileName);
    if (!fs.existsSync(stagedPath)) return null;
    try {
      await verifyFile(stagedPath, target.sha512, target.sizeBytes);
    } catch (_err) {
      await fs.promises.rm(stagedPath, { force: true });
      return null;
    }
    this.manifest = target;
    this.status(STATUS.READY, {
      manifest: target,
      stagedPath,
      installerPath: stagedPath,
      ready: true,
      latestVersion: target.version,
      updateAvailable: true,
      releaseNotes: target.releaseNotes,
      percentage: 100,
      message: `DFlash Console ${target.version} is ready to install.`,
      error: '',
    });
    return stagedPath;
  }

  async stageUpdate(manifest) {
    const target = manifest || this.manifest || await this.checkForUpdate();
    if (!target) throw new Error('No newer DFlash Console release is available.');
    const resumed = await this.resumeStagedUpdate(target);
    if (resumed) return resumed;
    return this.download(target);
  }

  async launchInstaller(stagedPath, options = {}) {
    if (!safeArtifactName(path.basename(stagedPath))) throw new Error('Unsafe staged DFlash update path');
    const readyFile = path.join(this.stagingDir, 'helper-ready.json');
    const quitFile = path.join(this.stagingDir, 'helper-quit.json');
    const launcherPath = path.join(this.stagingDir, 'launch-update-helper.ps1');
    const relaunchPath = options.relaunchPath || process.execPath;
    const versionMatch = path.basename(stagedPath).match(/DFlash-Console-Setup-([0-9.]+)-x64\.exe/i);
    const targetVersion = String(
      options.targetVersion
      || this.manifest?.version
      || this.getStatus().latestVersion
      || versionMatch?.[1]
      || '',
    ).trim();
    await Promise.all([fs.promises.rm(readyFile, { force: true }), fs.promises.rm(quitFile, { force: true })]);
    await launchDetachedUpdateHelper(this.helperPath, [
      ['-InstallerPath', path.resolve(stagedPath)],
      ['-TargetVersion', targetVersion],
      ['-InstallRoot', path.dirname(relaunchPath)],
      ['-ParentProcessId', String(options.processId || process.pid)],
      ['-ReadyFile', readyFile],
      ['-QuitReadyFile', quitFile],
    ], launcherPath);
    await this.waitForFile(readyFile, 15000);
    this.status(STATUS.INSTALLING, {
      stagedPath,
      installerPath: stagedPath,
      ready: false,
      message: 'Installing DFlash Console update…',
    });
    if (typeof options.onReady === 'function') await options.onReady();
    await fs.promises.writeFile(quitFile, JSON.stringify({ ready: true }), 'utf8');
    return true;
  }

  async waitForFile(filePath, timeoutMs) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (fs.existsSync(filePath)) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error('DFlash update helper did not complete its ready handshake');
  }
}

module.exports = { STATUS, UpdateService, safeArtifactName };
