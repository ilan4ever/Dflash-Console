'use strict';

const crypto = require('crypto');
const fs = require('fs');

function canonicalize(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function signedManifestPayload(manifest) {
  const unsigned = { ...manifest };
  delete unsigned.signature;
  return Buffer.from(canonicalize(unsigned), 'utf8');
}

function verifyManifestSignature(manifest, publicKey) {
  if (!publicKey) throw new Error('DFlash update public key is required');
  if (manifest.signatureAlgorithm !== 'RSA-SHA256') {
    throw new Error('Unsupported DFlash update signature algorithm');
  }
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(String(manifest.signature || ''))) {
    throw new Error('Update manifest signature is not valid base64');
  }
  let signature;
  try {
    signature = Buffer.from(manifest.signature, 'base64');
  } catch (_err) {
    throw new Error('Update manifest signature is not valid base64');
  }
  const valid = crypto.verify(
    'RSA-SHA256',
    signedManifestPayload(manifest),
    publicKey,
    signature,
  );
  if (!valid) throw new Error('DFlash update manifest signature verification failed');
  return true;
}

function createSha512() {
  return crypto.createHash('sha512');
}

async function verifyFile(filePath, expectedSha512, expectedSizeBytes) {
  const stat = await fs.promises.stat(filePath);
  if (stat.size !== expectedSizeBytes) {
    throw new Error(`Update size mismatch: expected ${expectedSizeBytes}, got ${stat.size}`);
  }
  const hash = createSha512();
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.once('error', reject);
    stream.once('end', resolve);
  });
  const digest = hash.digest('hex');
  if (digest.toLowerCase() !== String(expectedSha512).toLowerCase()) {
    throw new Error('DFlash update SHA-512 verification failed');
  }
  return { sizeBytes: stat.size, sha512: digest };
}

module.exports = {
  canonicalize,
  signedManifestPayload,
  verifyManifestSignature,
  verifyFile,
};
