import { chromium } from 'playwright';

const files = Array.from({ length: 48 }, (_, index) => ({
  filename: `model-${String(index + 1).padStart(5, '0')}-of-00048.safetensors`,
  size_bytes: index === 0 || index === 44 ? 1_010_000_000 : 3_300_000_000,
}));

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.route('**/api/hf/**', async (route) => {
  const url = route.request().url();
  if (url.includes('/api/hf/models/deepseek-ai/')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: {
          id: 'deepseek-ai/DeepSeek-V4-Flash-0731',
          title: 'DeepSeek-V4-Flash-0731',
          author: 'deepseek-ai',
          lab: 'DeepSeek',
          downloads_label: '4.3M',
          likes: 1,
          updated_ago: '28 days ago',
          size_label: '155 GB',
          size_gb: 155,
          tags: ['safetensors', 'transformers'],
          modality: 'llm',
          runnable: true,
          has_gguf: false,
          download_files: files,
          description: 'fixture',
        },
      }),
    });
    return;
  }
  if (url.includes('/api/hf/search')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        models: [{ id: 'deepseek-ai/DeepSeek-V4-Flash-0731', title: 'DeepSeek-V4-Flash-0731', author: 'deepseek-ai' }],
      }),
    });
    return;
  }
  await route.continue();
});
await page.goto('http://127.0.0.1:8900/', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForFunction(() => window.DFlashModelSearchLive?.revealRepo, null, { timeout: 60000 });
await page.evaluate(async () => {
  window.DFlashShell?.setView?.('catalog');
  await window.DFlashModelSearchLive?.onViewEnter?.();
  await window.DFlashModelSearchLive.revealRepo('deepseek-ai/DeepSeek-V4-Flash-0731');
});
await page.waitForFunction(() => document.querySelectorAll('#hfFilePick option').length === 1, null, { timeout: 60000 });
const options = await page.$$eval('#hfFilePick option', (els) => els.map((el) => el.textContent.trim()));
const hint = (await page.locator('.df-catalog-download-hint').textContent().catch(() => '')) || '';
console.log(JSON.stringify({ optionCount: options.length, options, hint }, null, 2));
if (options.length !== 1) {
  throw new Error(`Expected 1 grouped row, got ${options.length}`);
}
if (!options[0].includes('Full model') || !options[0].includes('total')) {
  throw new Error(`Unexpected option label: ${options[0]}`);
}
if (!hint.includes('48 files') || !hint.toLowerCase().includes('155')) {
  throw new Error(`Missing grouped download hint: ${hint}`);
}
await browser.close();
