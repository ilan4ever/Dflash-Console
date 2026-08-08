'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

function argument(name, fallback = '') {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function canonicalize(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

const installerPath = path.resolve(argument('--installer'));
const outputPath = path.resolve(argument('--output'));
const keyPath = path.resolve(argument('--key', path.join(
  process.env.USERPROFILE || process.env.HOME || '.',
  '.dflash-console',
  'dflash-update-private.pem',
)));
const version = argument('--version');
const downloadUrl = argument('--download-url');
const releaseNotes = argument('--release-notes', `DFlash Console ${version}`);
const keyId = argument('--key-id', 'dflash-rsa-2026-1');

if (!version || !downloadUrl || !fs.existsSync(installerPath)) {
  throw new Error('Usage: node tools/sign-update-manifest.js --installer <exe> --output <json> --version <version> --download-url <url>');
}
if (!fs.existsSync(keyPath)) throw new Error(`Update signing key not found: ${keyPath}`);

const stat = fs.statSync(installerPath);
const unsigned = {
  app: 'DFlash Console',
  appId: 'com.dflash.console',
  version,
  fileName: path.basename(installerPath),
  sizeBytes: stat.size,
  sha512: crypto.createHash('sha512').update(fs.readFileSync(installerPath)).digest('hex'),
  releaseNotes,
  downloadUrl,
  publishedAt: new Date().toISOString(),
  signatureAlgorithm: 'RSA-SHA256',
  signatureKeyId: keyId,
};
const signature = crypto.sign(
  'RSA-SHA256',
  Buffer.from(canonicalize(unsigned), 'utf8'),
  fs.readFileSync(keyPath, 'utf8'),
).toString('base64');

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify({ ...unsigned, signature }, null, 2)}\n`, 'utf8');
console.log(`Signed ${unsigned.fileName} as ${outputPath}`);
