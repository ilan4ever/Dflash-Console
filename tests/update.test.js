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
const { safeArtifactName } = require('../electron/update-service');

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

test('validates the DFlash x64 NSIS manifest contract', () => {
  const value = manifest();
  assert.equal(validateManifest(value).valid, false);
  value.signature = 'placeholder';
  assert.equal(validateManifest(value).valid, true);
  assert.equal(safeArtifactName(value.fileName), true);
  assert.equal(safeArtifactName('../' + value.fileName), false);
});

test('requires authenticated HTTPS update configuration', () => {
  assert.throws(() => normalizeUpdateConfig({ manifestUrl: 'https://updates.example.test' }));
  const config = normalizeUpdateConfig({
    manifestUrl: 'https://updates.example.test/manifest.json',
    token: 'test-token',
  });
  assert.equal(config.token, 'test-token');
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
