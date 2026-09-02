'use strict';

const path = require('path');

const DFLASH_APP_ID = 'com.dflash.console';
const DFLASH_APP_NAME = 'DFlash Console';
const DFLASH_PLATFORM = 'win32-x64';
const DFLASH_SETUP_FILE = /^DFlash-Console-Setup-[0-9]+\.[0-9]+\.[0-9]+-x64\.exe$/i;
const REQUIRED_MANIFEST_FIELDS = [
  'app',
  'appId',
  'version',
  'fileName',
  'sizeBytes',
  'sha512',
  'releaseNotes',
  'downloadUrl',
  'publishedAt',
  'signatureAlgorithm',
  'signatureKeyId',
  'signature',
];

function compareVersions(left, right) {
  const parse = (value) => String(value || '')
    .trim()
    .replace(/^v/i, '')
    .split('.')
    .map((part) => {
      const match = part.match(/^\d+/);
      return match ? Number(match[0]) : 0;
    });
  const a = parse(left);
  const b = parse(right);
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    if ((a[index] || 0) > (b[index] || 0)) return 1;
    if ((a[index] || 0) < (b[index] || 0)) return -1;
  }
  return 0;
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function validateManifest(manifest, options = {}) {
  const errors = [];
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
    return { valid: false, errors: ['manifest must be an object'] };
  }
  for (const field of REQUIRED_MANIFEST_FIELDS) {
    if (!(field in manifest)) errors.push(`missing ${field}`);
  }
  if (!isNonEmptyString(manifest.app) || manifest.app !== DFLASH_APP_NAME) {
    errors.push(`app must be "${DFLASH_APP_NAME}"`);
  }
  if (!isNonEmptyString(manifest.appId) || manifest.appId !== DFLASH_APP_ID) {
    errors.push(`appId must be "${DFLASH_APP_ID}"`);
  }
  if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(String(manifest.version || ''))) {
    errors.push('version must be semantic version text');
  }
  if (!isNonEmptyString(manifest.fileName) || !DFLASH_SETUP_FILE.test(manifest.fileName)) {
    errors.push('fileName must be a DFlash x64 setup artifact');
  }
  if (!Number.isSafeInteger(manifest.sizeBytes) || manifest.sizeBytes <= 0) {
    errors.push('sizeBytes must be a positive integer');
  }
  if (!/^[a-f0-9]{128}$/i.test(String(manifest.sha512 || ''))) {
    errors.push('sha512 must be a SHA-512 hex digest');
  }
  if (!isNonEmptyString(manifest.releaseNotes)) errors.push('releaseNotes must be text');
  for (const field of ['downloadUrl', 'publishedAt', 'signatureKeyId', 'signature']) {
    if (!isNonEmptyString(manifest[field])) errors.push(`${field} must be text`);
  }
  if (isNonEmptyString(manifest.publishedAt) && Number.isNaN(Date.parse(manifest.publishedAt))) {
    errors.push('publishedAt must be an ISO date');
  }
  try {
    const url = new URL(manifest.downloadUrl);
    if (!['https:', ...(options.allowHttp ? ['http:'] : [])].includes(url.protocol)) {
      errors.push('downloadUrl must use HTTPS');
    }
  } catch (_err) {
    errors.push('downloadUrl must be a valid URL');
  }
  if (manifest.signatureAlgorithm !== 'RSA-SHA256') {
    errors.push('signatureAlgorithm must be RSA-SHA256');
  }
  return { valid: errors.length === 0, errors };
}

function assertValidManifest(manifest, options) {
  const result = validateManifest(manifest, options);
  if (!result.valid) throw new Error(`Invalid DFlash update manifest: ${result.errors.join('; ')}`);
  return manifest;
}

function normalizeUpdateConfig(config = {}) {
  const manifestUrl = String(config.manifestUrl || process.env.DFLASH_UPDATE_MANIFEST_URL || '').trim();
  const token = String(config.token || process.env.DFLASH_UPDATE_TOKEN || '').trim();
  if (!manifestUrl) throw new Error('DFlash update manifest URL is required');
  const parsed = new URL(manifestUrl);
  if (!['https:', ...(config.allowHttp ? ['http:'] : [])].includes(parsed.protocol)) {
    throw new Error('DFlash update manifest URL must use HTTPS');
  }
  return {
    manifestUrl: parsed.toString(),
    token,
    publicKey: config.publicKey || process.env.DFLASH_UPDATE_PUBLIC_KEY || null,
    stagingDir: config.stagingDir ? path.resolve(config.stagingDir) : null,
    allowHttp: Boolean(config.allowHttp),
  };
}

module.exports = {
  DFLASH_APP_ID,
  DFLASH_APP_NAME,
  DFLASH_PLATFORM,
  DFLASH_SETUP_FILE,
  REQUIRED_MANIFEST_FIELDS,
  compareVersions,
  validateManifest,
  assertValidManifest,
  normalizeUpdateConfig,
};
