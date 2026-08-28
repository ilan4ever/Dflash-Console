/** First-run setup wizard — scan PC, confirm model library folders */
(function () {
  const { api, toast } = window.ConsoleApi;

  let candidates = [];
  let selectedPaths = new Set();
  let scanning = false;
  let opened = false;
  const engineInstalls = new Set();
  let transformersReady = false;
  let transformersBootstrapStarted = false;

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
    if (finish) finish.disabled = step !== 'review';
  }

  function updateSummary() {
    const summary = document.getElementById('setupWizardSummary');
    const meta = document.getElementById('setupWizardMeta');
    const selected = candidates.filter((row) => selectedPaths.has(row.path));
    const models = selected.reduce((sum, row) => sum + Number(row.model_count || 0), 0);
    if (summary) {
      summary.textContent = selected.length
        ? `${selected.length} folder${selected.length === 1 ? '' : 's'} selected · about ${models} model file${models === 1 ? '' : 's'}`
        : 'No folders selected — you can skip and download models later.';
    }
    if (meta) {
      meta.textContent = `${candidates.length} location${candidates.length === 1 ? '' : 's'} found on this PC`;
    }
    const finish = document.getElementById('setupWizardFinish');
    if (finish) finish.disabled = false;
    const selectAll = document.getElementById('setupWizardSelectAll');
    if (selectAll) {
      selectAll.checked = candidates.length > 0 && selected.length === candidates.length;
      selectAll.indeterminate = selected.length > 0 && selected.length < candidates.length;
    }
  }

  function sortCandidates(rows) {
    return rows.slice().sort((a, b) => {
      const score = (row) => {
        if (row.model_type === 'gguf' || row.preset === 'lmstudio' || row.preset === 'dflash') return 0;
        if (isHfCachePath(row.path)) return 2;
        return 1;
      };
      const diff = score(a) - score(b);
      if (diff !== 0) return diff;
      return Number(b.model_count || 0) - Number(a.model_count || 0);
    });
  }

  function renderCandidates() {
    const list = document.getElementById('setupWizardResults');
    if (!list) return;
    const rows = sortCandidates(candidates);
    const ggufRows = rows.filter((row) => row.model_type === 'gguf' || row.preset === 'lmstudio' || row.preset === 'dflash');
    const ggufCount = ggufRows.reduce((sum, row) => sum + Number(row.model_count || 0), 0);
    const ggufSamples = ggufRows.flatMap((row) => row.sample_models || []).slice(0, 4);
    if (!rows.length) {
      list.innerHTML = '<p class="lm-setting-desc">No model folders were found yet. You can finish with the default Console models folder and add more later in Settings.</p>';
      updateSummary();
      return;
    }
    const highlight = ggufCount > 0
      ? `<div class="lm-setup-gguf-highlight">
          <strong>${ggufCount} GGUF checkpoint${ggufCount === 1 ? '' : 's'} found</strong>
          <div class="lm-setup-gguf-samples">${ggufSamples.map((s) => `<span class="lm-tag green">${escapeHtml(s)}</span>`).join(' ') || '<span class="lm-tag dim">Check the folders below</span>'}</div>
        </div>`
      : '<div class="lm-setup-gguf-highlight is-empty"><strong>No GGUF folders found yet</strong><span class="lm-setting-desc">You can still add LM Studio or other folders later in Settings.</span></div>';
    list.innerHTML = highlight + rows.map((row) => {
      const checked = selectedPaths.has(row.path);
      const type = TYPE_LABELS[row.model_type] || TYPE_LABELS.unknown;
      const count = Number(row.model_count || 0);
      const isGguf = row.model_type === 'gguf' || row.preset === 'lmstudio' || row.preset === 'dflash';
      const samples = (row.sample_models || []).slice(0, isGguf ? 4 : 2).map((s) => `<span class="lm-tag dim">${escapeHtml(s)}</span>`).join(' ');
      const suggested = row.suggested ? '<span class="lm-tag green">suggested</span>' : '';
      return `
        <label class="lm-setup-row${checked ? ' selected' : ''}${isGguf ? ' is-gguf' : ''}">
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

  function isHfCachePath(path) {
    return String(path || '').replace(/\\/g, '/').toLowerCase().includes('/.cache/huggingface');
  }

  function applyDefaultSelection() {
    // Pre-check every folder that actually holds models — the user only has to
    // approve the scan result (or uncheck what they do not want).
    selectedPaths = new Set(
      candidates.filter((row) => Number(row.model_count || 0) > 0).map((row) => row.path),
    );
    if (!selectedPaths.size && candidates.length) {
      candidates.slice(0, 3).forEach((row) => selectedPaths.add(row.path));
    }
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
      applyDefaultSelection();
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
    // Every folder the user approved is added — including the Hugging Face
    // cache, which holds the SafeTensors models the Transformers engine loads.
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

  function updateEngineFinishState() {
    const finish = document.getElementById('setupWizardFinishEngines');
    if (!finish) return;
    const installing = engineInstalls.size > 0;
    if (transformersReady) {
      finish.disabled = false;
      finish.textContent = 'Finish setup';
      return;
    }
    finish.disabled = installing || !transformersBootstrapStarted;
    finish.textContent = installing ? 'Installing Transformers…' : 'Waiting for Transformers…';
  }

  function startTransformersEngine() {
    // Bring the worker up right away so the Engines tab shows Transformers on
    // after setup — not only after the next app restart.
    void api('/api/runtimes/transformers/start', { method: 'POST', timeoutMs: 120000 })
      .then(() => window.DFlashServerLive?.refresh?.(true, { fresh: true }))
      .catch(() => {});
  }

  async function pollEngineStatus(runtimeId, statusEl, buttonEl, readyText, { required = false } = {}) {
    if (!statusEl) return;
    try {
      const data = await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/install`, { timeoutMs: 8000 });
      const status = String(data?.status || (data?.installed ? 'installed' : 'idle'));
      if (data?.installed) {
        engineInstalls.delete(runtimeId);
        statusEl.textContent = readyText;
        if (buttonEl) {
          buttonEl.disabled = false;
          buttonEl.hidden = runtimeId === 'transformers';
          buttonEl.textContent = runtimeId === 'transformers' ? 'Installed' : buttonEl.textContent;
        }
        if (runtimeId === 'transformers') {
          if (!transformersReady) startTransformersEngine();
          transformersReady = true;
        }
      } else if (status === 'installing') {
        engineInstalls.add(runtimeId);
        statusEl.textContent = 'Downloading… this can take several minutes.';
        if (buttonEl) buttonEl.disabled = true;
        window.setTimeout(() => void pollEngineStatus(runtimeId, statusEl, buttonEl, readyText, { required }), 4000);
      } else if (status === 'error') {
        engineInstalls.delete(runtimeId);
        statusEl.textContent = data?.error || 'Install failed. You can retry.';
        if (buttonEl) {
          buttonEl.hidden = false;
          buttonEl.disabled = false;
          buttonEl.textContent = 'Retry';
        }
        if (required) transformersReady = false;
      } else {
        engineInstalls.delete(runtimeId);
        statusEl.textContent = required ? 'Preparing install…' : 'Not installed yet.';
        if (buttonEl) buttonEl.disabled = false;
      }
    } catch (err) {
      statusEl.textContent = err.message || 'Could not read install status.';
      if (required) transformersReady = false;
    }
    updateEngineFinishState();
  }

  async function startEngineInstall(runtimeId, extra = {}) {
    engineInstalls.add(runtimeId);
    updateEngineFinishState();
    await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(extra),
      timeoutMs: 15000,
    });
  }

  async function ensureTransformersInstalled() {
    if (transformersBootstrapStarted) return;
    transformersBootstrapStarted = true;
    updateEngineFinishState();
    const statusEl = document.getElementById('setupTfStatus');
    const buttonEl = document.getElementById('setupTfInstall');
    try {
      const data = await api('/api/runtimes/transformers/install', { timeoutMs: 8000 });
      if (data?.installed) {
        if (!transformersReady) startTransformersEngine();
        transformersReady = true;
        if (statusEl) statusEl.textContent = 'Ready — Transformers can load Hugging Face models.';
        updateEngineFinishState();
        return;
      }
      if (data?.status !== 'installing') {
        await startEngineInstall('transformers', { torch_variant: 'auto' });
        if (statusEl) statusEl.textContent = 'Downloading Transformers… this can take several minutes.';
      }
    } catch (err) {
      if (statusEl) statusEl.textContent = err.message || 'Could not start Transformers install.';
      if (buttonEl) buttonEl.hidden = false;
      transformersBootstrapStarted = false;
    }
    void pollEngineStatus(
      'transformers',
      statusEl,
      buttonEl,
      'Ready — Transformers can load Hugging Face models.',
      { required: true },
    );
  }

  function refreshEngineCards() {
    void pollEngineStatus(
      'vllm',
      document.getElementById('setupVllmStatus'),
      document.getElementById('setupVllmInstall'),
      'Ready — choose vLLM when you load a Hugging Face model.',
    );
    void ensureTransformersInstalled();
  }

  async function completeSetupAndOpenEngines() {
    setStep('engines');
    const subtitle = document.getElementById('setupWizardSubtitle');
    if (subtitle) subtitle.textContent = 'Installing required engines for this PC. Transformers starts automatically.';
    refreshEngineCards();
  }

  function clearPostInstallFlags() {
    window.DFlashDesktop?.setAppSettings?.({ postInstallSetup: false, postInstallWelcome: false });
  }

  async function finishSetup({ close = false } = {}) {
    const finish = document.getElementById('setupWizardFinish');
    if (finish) finish.disabled = true;
    try {
      await api('/api/setup/complete', {
        method: 'POST',
        body: JSON.stringify({ libraries: librariesPayload() }),
        timeoutMs: 30000,
      });
      if (window.DFlashSettingsLive?.refresh) void window.DFlashSettingsLive.refresh();
      if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
      if (close) {
        closeModal();
        toast('Setup saved.');
        return;
      }
      await completeSetupAndOpenEngines();
      clearPostInstallFlags();
    } catch (err) {
      toast(err.message || 'Could not save setup', false);
      if (finish) finish.disabled = false;
    }
  }

  async function finishEngineSetup() {
    if (!transformersReady) {
      toast('Wait for Transformers to finish installing.', false);
      return;
    }
    clearPostInstallFlags();
    closeModal();
    toast('Setup complete. Transformers is ready.');
    startTransformersEngine();
    if (window.DFlashSettingsLive?.refresh) void window.DFlashSettingsLive.refresh();
    if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh({ force: true });
  }

  async function maybeOpen() {
    if (opened) return;
    try {
      let postInstallSetup = false;
      if (window.DFlashDesktop?.getAppSettings) {
        try {
          const settings = await window.DFlashDesktop.getAppSettings();
          postInstallSetup = Boolean(settings?.postInstallSetup);
        } catch {
          postInstallSetup = false;
        }
      }
      const health = await api('/api/health', { timeoutMs: 15000 });
      if (health?.setup_complete) {
        // Setup already finished — never reopen the folder wizard. Only finish
        // a missing required engine install, and clear a stale post-install
        // flag so the wizard does not flash open and closed again.
        const tf = await api('/api/runtimes/transformers/install', { timeoutMs: 8000 }).catch(() => null);
        if (tf && !tf.installed && tf.status !== 'installing') {
          opened = true;
          openModal();
          setStep('engines');
          const subtitle = document.getElementById('setupWizardSubtitle');
          if (subtitle) subtitle.textContent = 'Transformers is still required on this PC. Finishing installation now.';
          refreshEngineCards();
        } else if (postInstallSetup) {
          clearPostInstallFlags();
        }
        return;
      }
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
    document.getElementById('setupWizardSkipFolders')?.addEventListener('click', () => {
      selectedPaths.clear();
      void finishSetup();
    });
    document.getElementById('setupWizardFinishEngines')?.addEventListener('click', () => {
      void finishEngineSetup();
    });
    document.getElementById('setupVllmInstall')?.addEventListener('click', () => {
      void startEngineInstall('vllm', { backend: 'auto' }).then(() => {
        toast('vLLM install started');
        refreshEngineCards();
      }).catch((err) => {
        engineInstalls.delete('vllm');
        updateEngineFinishState();
        toast(err.message || 'Could not start vLLM install', false);
      });
    });
    document.getElementById('setupTfInstall')?.addEventListener('click', () => {
      transformersBootstrapStarted = false;
      transformersReady = false;
      void startEngineInstall('transformers', { torch_variant: 'auto' }).then(() => {
        toast('Transformers install restarted');
        transformersBootstrapStarted = true;
        refreshEngineCards();
      }).catch((err) => {
        engineInstalls.delete('transformers');
        transformersBootstrapStarted = false;
        updateEngineFinishState();
        toast(err.message || 'Could not start Transformers install', false);
      });
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
