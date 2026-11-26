'use strict';

// The one-time walk of the whole data root (104 s on OMAN) was cut off when
// the app closed before it ended, and only ran again at the next runtime
// refresh. It now stamps itself and is retried at every start until done.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { repairWholeDataRootOnce } = require('../electron/bootstrap');

const STAMP = '.install-access-full';
const RUNNING = '.install-access-full.running';
const windowsOnly = { skip: process.platform !== 'win32' ? 'needs Windows PowerShell' : false, timeout: 60000 };

async function waitFor(check, ms = 30000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (check()) return true;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

function fakeScript(dir, body) {
  const script = path.join(dir, "set install's permissions.ps1");
  fs.writeFileSync(script, `param($Path, $Kind, [switch]$Recurse)\n${body}\n`);
  return script;
}

test('a finished walk stamps itself, and is not started again', windowsOnly, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dflash walk '"));
  try {
    const script = fakeScript(root, "Set-Content -LiteralPath (Join-Path $Path 'walked') -Value $Kind\nexit 0");
    assert.equal(repairWholeDataRootOnce(root, script), true);
    assert.ok(await waitFor(() => fs.existsSync(path.join(root, STAMP))), 'stamp written by the walk itself');
    assert.ok(await waitFor(() => !fs.existsSync(path.join(root, RUNNING))));
    assert.equal(fs.readFileSync(path.join(root, 'walked'), 'utf8').trim(), 'data');
    assert.equal(repairWholeDataRootOnce(root, script), false, 'done once');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a failed walk leaves no stamp, so the next start tries again', windowsOnly, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dflash-walk-'));
  try {
    for (const body of ['exit 3', "throw 'access denied'"]) {
      const script = fakeScript(root, body);
      assert.equal(repairWholeDataRootOnce(root, script), true);
      assert.ok(await waitFor(() => !fs.existsSync(path.join(root, RUNNING))), body);
      assert.equal(fs.existsSync(path.join(root, STAMP)), false, body);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a walk still running from a recent start is left to finish', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dflash-walk-'));
  try {
    fs.writeFileSync(path.join(root, RUNNING), 'now');
    assert.equal(repairWholeDataRootOnce(root, path.join(root, 'missing.ps1')), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
