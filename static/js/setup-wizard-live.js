/** First-run setup wizard — scan PC, confirm model library folders */
(function () {
  const { api, toast } = window.ConsoleApi;

  let candidates = [];
  let selectedPaths = new Set();
  let scanning = false;
  let opened = false;

  const TYPE_LABELS = {
    gguf: 'GGUF',
    piper: 'Piper TTS',
    whisper: 'Whisper STT',
    hub: 'Hugging Face cache',
    ocr: 'OCR',
    embeddings: 'Embeddings',
    unknown: 'Models',
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function modal() {
    return document.getElementById('setupWizardModal');
  }

  function openModal() {
    const el = modal();
    if (!el) return;
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
  }

  function closeModal() {
    const el = modal();
    if (!el) return;
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) {
      document.body.classList.remove('modal-open');
    }
  }

  function setStep(step) {
    document.querySelectorAll('[data-setup-step]').forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.setupStep === step);
    });
    const finish = document.getElementById('setupWizardFinish');
    if (finish) finish.disabled = step !== 'review' || selectedPaths.size === 0;
  }

  function updateSummary() {
    const summary = document.getElementById('setupWizardSummary');
    const meta = document.getElementById('setupWizardMeta');
    const selected = candidates.filter((row) => selectedPaths.has(row.path));
    const models = selected.reduce((sum, row) => sum + Number(row.model_count || 0), 0);
    if (summary) {
      summary.textContent = selected.length
        ? `${selected.length} folder${selected.length === 1 ? '' : 's'} selected · about ${models} model file${models === 1 ? '' : 's'}`
        : 'Select at least one folder to continue.';
    }
    if (meta) {
      meta.textContent = `${candidates.length} location${candidates.length === 1 ? '' : 's'} found on this PC`;
    }
    const finish = document.getElementById('setupWizardFinish');
    if (finish) finish.disabled = !selected.length;
    const selectAll = document.getElementById('setupWizardSelectAll');
    if (selectAll) {
      selectAll.checked = candidates.length > 0 && selected.length === candidates.length;
      selectAll.indeterminate = selected.length > 0 && selected.length < candidates.length;
    }
  }

  function renderCandidates() {
    const list = document.getElementById('setupWizardResults');
    if (!list) return;
    if (!candidates.length) {
      list.innerHTML = '<p class="lm-setting-desc">No model folders were found yet. You can finish with the default Console models folder and add more later in Settings.</p>';
      updateSummary();
      return;
    }
    list.innerHTML = candidates.map((row) => {
      const checked = selectedPaths.has(row.path);
      const type = TYPE_LABELS[row.model_type] || TYPE_LABELS.unknown;
      const count = Number(row.model_count || 0);
      const samples = (row.sample_models || []).slice(0, 2).map((s) => `<span class="lm-tag dim">${escapeHtml(s)}</span>`).join(' ');
      const suggested = row.suggested ? '<span class="lm-tag green">suggested</span>' : '';
      return `
        <label class="lm-setup-row${checked ? ' selected' : ''}">
          <input type="checkbox" data-setup-path="${escapeHtml(row.path)}"${checked ? ' checked' : ''}>
          <div class="lm-setup-row-main">
            <div class="lm-setup-row-title">${escapeHtml(row.label || row.path)} ${suggested}</div>
            <code class="lm-settings-path">${escapeHtml(row.path)}</code>
            <div class="lm-setup-row-meta">${escapeHtml(type)} · ${count} item${count === 1 ? '' : 's'} ${samples}</div>
          </div>
        </label>`;
    }).join('');

    list.querySelectorAll('[data-setup-path]').forEach((input) => {
      input.addEventListener('change', () => {
        const path = input.dataset.setupPath;
        if (input.checked) selectedPaths.add(path);
        else selectedPaths.delete(path);
        input.closest('.lm-setup-row')?.classList.toggle('selected', input.checked);
        updateSummary();
      });
    });
    updateSummary();
  }

  async function runScan() {
    if (scanning) return;
    scanning = true;
    setStep('scan');
    const subtitle = document.getElementById('setupWizardSubtitle');
    if (subtitle) subtitle.textContent = 'Scanning LM Studio, Hugging Face cache, and common model folders…';
    try {
      const data = await api('/api/setup/scan', { timeoutMs: 120000 });
      candidates = Array.isArray(data.candidates) ? data.candidates : [];
      selectedPaths = new Set(
        candidates.filter((row) => row.suggested).map((row) => row.path),
      );
      if (!selectedPaths.size && candidates.length) {
        candidates.slice(0, 3).forEach((row) => selectedPaths.add(row.path));
      }
      renderCandidates();
      setStep('review');
      if (subtitle) {
        const summary = data.summary || {};
        subtitle.textContent = `Found ${summary.folder_count || candidates.length} folders with about ${summary.model_count || 0} model files. Confirm where DFlash should scan.`;
      }
    } catch (err) {
      if (subtitle) subtitle.textContent = err.message || 'Scan failed. You can retry or continue with defaults.';
      candidates = [];
      selectedPaths = new Set();
      renderCandidates();
      setStep('review');
    } finally {
      scanning = false;
    }
  }

  function librariesPayload() {
    const selected = candidates.filter((row) => selectedPaths.has(row.path));
    if (!selected.length) {
      return [{
        id: 'dflash-checkpoints',
        label: 'DFlash Console models',
        preset: 'dflash',
        enabled: true,
        download_default: true,
      }];
    }
    return selected.map((row, index) => ({
      id: `${row.preset || 'custom'}-setup-${index}`,
      label: row.label || row.path,
      path: row.path,
      preset: row.preset || 'custom',
      enabled: true,
      download_default: index === 0,
    }));
  }

  async function finishSetup() {
    const finish = document.getElementById('setupWizardFinish');
    if (finish) finish.disabled = true;
    try {
      await api('/api/setup/complete', {
        method: 'POST',
        body: JSON.stringify({ libraries: librariesPayload() }),
        timeoutMs: 30000,
      });
      closeModal();
      toast('Library locations saved.');
      if (window.DFlashSettingsLive?.refresh) void window.DFlashSettingsLive.refresh();
      if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
    } catch (err) {
      toast(err.message || 'Could not save setup', false);
      if (finish) finish.disabled = false;
    }
  }

  async function maybeOpen() {
    if (opened) return;
    try {
      const health = await api('/api/health', { timeoutMs: 15000 });
      if (health?.setup_complete) return;
      opened = true;
      openModal();
      setStep('welcome');
      void runScan();
    } catch {
      /* API not ready yet */
    }
  }

  function bind() {
    document.getElementById('setupWizardStart')?.addEventListener('click', () => {
      void runScan();
    });
    document.getElementById('setupWizardRescan')?.addEventListener('click', () => {
      void runScan();
    });
    document.getElementById('setupWizardFinish')?.addEventListener('click', () => {
      void finishSetup();
    });
    document.getElementById('setupWizardSelectAll')?.addEventListener('change', (event) => {
      if (event.target.checked) {
        candidates.forEach((row) => selectedPaths.add(row.path));
      } else {
        selectedPaths.clear();
      }
      renderCandidates();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    window.setTimeout(() => { void maybeOpen(); }, 600);
  });

  window.DFlashSetupWizard = {
    open: () => {
      opened = true;
      openModal();
      setStep('welcome');
      void runScan();
    },
    maybeOpen,
  };
})();
