/* eslint-disable no-console */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'model-search-live.js'), 'utf8');
const start = source.indexOf('function formatCatalogFileSize');
const end = source.indexOf('function currentCategory');
const chunk = `${source.slice(start, end)}
module.exports = {
  catalogDownloadOptions,
  catalogDownloadOptionLabel,
  catalogDownloadHint,
  catalogDownloadFieldLabel,
};
`;

const sandbox = { module: { exports: {} }, window: { DFlashDownloadQueue: { formatBytes: (n) => `${(n / (1024 ** 3)).toFixed(2)} GB` } } };
vm.runInNewContext(chunk, sandbox);
const api = sandbox.module.exports;

function makeShard(index, sizeBytes) {
  return {
    filename: `model-${String(index).padStart(5, '0')}-of-00048.safetensors`,
    size_bytes: sizeBytes,
  };
}

const deepseek = {
  download_files: Array.from({ length: 48 }, (_, index) => makeShard(index + 1, index === 0 ? 1_010_000_000 : 3_300_000_000)),
};

const options = api.catalogDownloadOptions(deepseek);
if (options.length !== 1) {
  throw new Error(`expected 1 grouped download row, got ${options.length}`);
}
const label = api.catalogDownloadOptionLabel(options[0]);
if (!label.includes('Full model') || !label.includes('total')) {
  throw new Error(`unexpected grouped label: ${label}`);
}
if (api.catalogDownloadFieldLabel(options) !== 'Download') {
  throw new Error('sharded full model should use Download label');
}

const laguna = {
  download_files: [
    { filename: 'Laguna-S-2.1-UD-Q4_K_M-00001-of-00003.gguf', size_bytes: 3_700_000 },
    { filename: 'Laguna-S-2.1-UD-Q4_K_M-00002-of-00003.gguf', size_bytes: 50_000_000_000 },
    { filename: 'Laguna-S-2.1-UD-Q4_K_M-00003-of-00003.gguf', size_bytes: 23_000_000_000 },
    { filename: 'Laguna-S-2.1-UD-Q8_0.gguf', size_bytes: 120_000_000_000 },
  ],
};
const lagunaOptions = api.catalogDownloadOptions(laguna);
if (lagunaOptions.length !== 2) {
  throw new Error(`expected 2 quant rows, got ${lagunaOptions.length}`);
}

console.log('catalog download option tests passed');
