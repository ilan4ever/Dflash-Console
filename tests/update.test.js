'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const {
  DFLASH_APP_ID,
  DFLASH_APP_NAME,
  validateManifest,
  normalizeUpdateConfig,
} = require('../electron/update-contract');
const {
  signedManifestPayload,
  verifyManifestSignature,
} = require('../electron/update-integrity');
const { UpdateService, safeArtifactName } = require('../electron/update-service');
const packageJson = require('../package.json');

function manifest() {
  return {
    app: DFLASH_APP_NAME,
    appId: DFLASH_APP_ID,
    version: '0.0.74',
    fileName: 'DFlash-Console-Setup-0.0.74-x64.exe',
    sizeBytes: 10,
    sha512: 'a'.repeat(128),
    releaseNotes: 'Test release',
    downloadUrl: 'https://updates.example.test/DFlash-Console-Setup-0.0.74-x64.exe',
    publishedAt: '2026-08-06T00:00:00Z',
    signatureAlgorithm: 'RSA-SHA256',
    signatureKeyId: 'test-key',
  };
}

test('validates the DFlash x64 setup manifest contract', () => {
  const value = manifest();
  assert.equal(validateManifest(value).valid, false);
  value.signature = 'placeholder';
  assert.equal(validateManifest(value).valid, true);
  assert.equal(safeArtifactName(value.fileName), true);
  assert.equal(safeArtifactName('../' + value.fileName), false);
});

test('setup UI bakes package.json version instead of the installed folder label', () => {
  const builder = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'build-fast-installer.ps1'), 'utf8');
  const setupBuild = fs.readFileSync(path.join(__dirname, '..', 'tools', 'dflash-setup-ui', 'build.ps1'), 'utf8');
  const setupForm = fs.readFileSync(path.join(__dirname, '..', 'tools', 'dflash-setup-ui', 'SetupForm.cs'), 'utf8');
  assert.match(builder, /install-version\.txt/);
  assert.match(setupBuild, /SetupVersion\.cs/);
  assert.match(setupBuild, /package\.json/);
  assert.match(setupForm, /SetupVersion\.Value/);
  assert.match(setupForm, /already installed/);
  assert.doesNotMatch(setupForm, /foreach \(string dir in new\[\] \{ installRoot, uiRoot \}\)/);
});

test('uses the branded setup UI instead of the Windows NSIS wizard', () => {
  const targets = packageJson.build?.win?.target || [];
  assert.equal(targets.some((target) => target.target === 'nsis'), false);
  assert.equal(packageJson.scripts?.dist, 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-fast-installer.ps1');
  const builder = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'build-fast-installer.ps1'), 'utf8');
  assert.match(builder, /dflash-setup-ui\.exe/);
  assert.match(builder, /7zS2\.sfx/);
});

test('supports public and authenticated HTTPS update configurations', () => {
  const publicConfig = normalizeUpdateConfig({
    manifestUrl: 'https://github.com/ilan4ever/Dflash-Console/releases/latest/download/latest.json',
  });
  assert.equal(publicConfig.token, '');
  const config = normalizeUpdateConfig({
    manifestUrl: 'https://updates.example.test/manifest.json',
    token: 'test-token',
  });
  assert.equal(config.token, 'test-token');
});

test('public GitHub updates do not require a feed token', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  assert.match(main, /if \(!manifestUrl\) return null;/);
  assert.doesNotMatch(main, /if \(!manifestUrl \|\| !token\)/);
});

test('Windows startup registration uses the installed executable and startup marker', () => {
  const settings = fs.readFileSync(path.join(__dirname, '..', 'electron', 'app-settings.js'), 'utf8');
  assert.match(settings, /setLoginItemSettings/);
  assert.match(settings, /path: exe/);
  assert.match(settings, /STARTUP_ARGS/);
  assert.match(settings, /startupRegistrationState/);
});

test('does not attach authentication headers to public update requests', () => {
  const publicService = new UpdateService({
    manifestUrl: 'https://github.com/ilan4ever/Dflash-Console/releases/latest/download/latest.json',
  });
  assert.deepEqual(publicService.requestHeaders('application/json'), {
    Accept: 'application/json',
  });

  const authenticatedService = new UpdateService({
    manifestUrl: 'https://updates.example.test/manifest.json',
    token: 'test-token',
  });
  assert.deepEqual(authenticatedService.requestHeaders('application/json'), {
    Accept: 'application/json',
    Authorization: 'Bearer test-token',
  });
});

test('verifies RSA-SHA256 signatures over canonical unsigned manifests', () => {
  const keys = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const value = manifest();
  value.signature = crypto.sign('RSA-SHA256', signedManifestPayload(value), keys.privateKey).toString('base64');
  assert.equal(verifyManifestSignature(value, keys.publicKey), true);
  value.version = '0.0.75';
  assert.throws(() => verifyManifestSignature(value, keys.publicKey));
});

test('keeps file staging isolated from the final artifact until verified', async () => {
  const temp = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'dflash-update-test-'));
  assert.match(temp, /dflash-update-test-/);
  await fs.promises.rm(temp, { recursive: true, force: true });
});

test('owns the ready prompt in the dark renderer instead of native Electron dialogs', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
  const settings = fs.readFileSync(
    path.join(__dirname, '..', 'static', 'js', 'app-settings-live.js'),
    'utf8',
  );
  const index = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');

  assert.doesNotMatch(main, /promptInstallUpdate|DFlash Console update ready/);
  assert.match(settings, /desktopUpdateModal/);
  assert.match(settings, /updatePromptVersion/);
  assert.match(settings, /localStorage\.setItem\(UPDATE_PROMPT_VERSION_KEY, version\)/);
  assert.match(index, /id="desktopUpdateModal"/);
});
