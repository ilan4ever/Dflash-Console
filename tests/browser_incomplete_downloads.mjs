import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const errors = [];

page.on('pageerror', (err) => errors.push(String(err)));

await page.goto('http://127.0.0.1:8900/', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForFunction(() => window.DFlashDownloadQueue?.refresh && window.DFlashShell?.setView, null, { timeout: 60000 });

// Refresh downloads so incomplete repos are discovered.
await page.evaluate(async () => {
  await window.DFlashDownloadQueue.refresh({ discover: true });
});

const jobs = await page.evaluate(() => {
  const all = window.DFlashDownloadQueue.getJobs?.() || [];
  return all.map((job) => ({
    id: job.id,
    repo_id: job.repo_id,
    status: job.status,
    shard_present: job.shard_present,
    shard_total: job.shard_total,
    resumable: job.resumable,
  }));
});

const incomplete = jobs.filter((job) => job.status === 'incomplete');
console.log(JSON.stringify({ jobCount: jobs.length, incomplete }, null, 2));

if (!incomplete.some((job) => String(job.repo_id || '').includes('DeepSeek-V4-Flash-0731'))) {
  throw new Error('DeepSeek incomplete job missing from download queue');
}

// Downloads page — active pane should list incomplete with Resume.
await page.evaluate(() => {
  window.DFlashShell.setView('downloads');
  window.DFlashDownloadsLive?.showPane?.('active');
  window.DFlashDownloadsLive?.onViewEnter?.();
});
await page.waitForTimeout(1500);

const downloadsUi = await page.evaluate(() => {
  const cards = [...document.querySelectorAll('#dfDownloadsPageList .df-downloads-card')].map((card) => ({
    id: card.dataset.downloadJobId || '',
    title: card.querySelector('.df-downloads-card-title')?.textContent?.trim() || '',
    status: card.querySelector('.df-downloads-card-stat-primary')?.textContent?.trim() || '',
    hasResume: !!card.querySelector('[data-resume-job]'),
  }));
  const hint = document.getElementById('dfDownloadsPageHint')?.textContent?.trim() || '';
  return { hint, cards };
});
console.log('DOWNLOADS', JSON.stringify(downloadsUi, null, 2));

const deepseekCard = downloadsUi.cards.find((card) => /deepseek/i.test(card.title) || /deepseek/i.test(card.id));
if (!deepseekCard) {
  throw new Error('DeepSeek card missing on Downloads active pane');
}
if (!deepseekCard.hasResume) {
  throw new Error('DeepSeek card missing Resume button on Downloads page');
}
if (!/incomplete/i.test(deepseekCard.status)) {
  throw new Error(`DeepSeek status not incomplete: ${deepseekCard.status}`);
}

// Models library should show incomplete download row with Resume.
await page.evaluate(() => {
  window.DFlashShell.setView('models');
  return window.DFlashModelsLive?.onViewEnter?.();
});
await page.waitForTimeout(2500);

const modelsUi = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#modelsTableBody tr.downloading-model')].map((row) => ({
    id: row.dataset.downloadJobId || '',
    text: row.innerText.replace(/\s+/g, ' ').trim(),
    hasResume: !!row.querySelector('[data-action="resume-download"]'),
    incompleteClass: row.classList.contains('incomplete-download'),
  }));
  const filter = document.getElementById('modelsFilterInput');
  if (filter) {
    filter.value = 'DeepSeek-V4-Flash-0731';
    filter.dispatchEvent(new Event('input', { bubbles: true }));
  }
  return {
    downloadRows: rows,
    afterFilter: [...document.querySelectorAll('#modelsTableBody tr')].map((row) => row.innerText.replace(/\s+/g, ' ').trim()).slice(0, 8),
  };
});
console.log('MODELS', JSON.stringify(modelsUi, null, 2));

const modelsDeepseek = modelsUi.downloadRows.find((row) => /deepseek/i.test(row.id) || /deepseek/i.test(row.text));
if (!modelsDeepseek) {
  throw new Error('DeepSeek incomplete row missing from Models downloading list');
}
if (!modelsDeepseek.hasResume) {
  throw new Error('DeepSeek Models row missing Resume button');
}

if (errors.length) {
  console.log('PAGE_ERRORS', errors.slice(0, 5));
}

console.log('BROWSER_CHECK_PASSED');
await browser.close();
