/** On-demand runtime install prompts — vLLM, Transformers, etc. without opening Settings. */
(function () {
  const { api, toast } = window.ConsoleApi;

  const ON_DEMAND = new Set(['vllm', 'transformers', 'freetoken']);

  const META = {
    vllm: {
      label: 'vLLM engine',
      short: 'vLLM',
      blurb: 'vLLM runs Hugging Face SafeTensors models on your GPU. It is not included in the installer and must be downloaded first.',
      note: 'NVIDIA GPU required. On Windows this may install through WSL. Several GB — can take 10 minutes or more.',
      progress: 'Downloading and installing vLLM…',
    },
    transformers: {
      label: 'Transformers / PyTorch runtime',
      short: 'Transformers',
      blurb: 'Transformers loads SafeTensors models on CPU or GPU. PyTorch and dependencies download on first use.',
      note: 'Installation can take several minutes.',
      progress: 'Installing PyTorch and Transformers…',
    },
    freetoken: {
      label: 'FreeToken WSL engine',
      short: 'FreeToken',
      blurb: 'FreeToken runs supported MoE Hugging Face models through WSL2 with NVIDIA CUDA. It is not included in the installer.',
      note: 'Requires WSL2 Ubuntu and an NVIDIA driver with CUDA support. Large Linux packages — can take 15 minutes or more.',
      progress: 'Installing FreeToken in WSL…',
    },
  };

  let modalMode = 'hidden';

  function modal() {
    return document.getElementById('componentInstallModal');
  }

  function meta(runtimeId) {
    return META[runtimeId] || {
      label: runtimeId,
      short: runtimeId,
      blurb: 'This component must be installed before you can load models with it.',
      note: 'Installation can take several minutes.',
      progress: 'Installing…',
    };
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
    if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
    modalMode = 'hidden';
  }

  function setProgressVisible(visible) {
    document.getElementById('componentInstallProgress')?.classList.toggle('hidden', !visible);
    document.getElementById('componentInstallConfirmBody')?.classList.toggle('hidden', visible);
    document.getElementById('componentInstallFooterConfirm')?.classList.toggle('hidden', visible);
  }

  function setProgressText(text) {
    const el = document.getElementById('componentInstallProgressText');
    if (el) el.textContent = text || 'Installing…';
  }

  function formatElapsed(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total < 60) return `${total}s`;
    const minutes = Math.floor(total / 60);
    const rest = total % 60;
    return `${minutes}m ${rest}s`;
  }

  function renderInstallProgress(runtimeId, row = {}) {
    const info = meta(runtimeId);
    const raw = Number(row.progress);
    const elapsed = Number(row.elapsed_s || 0);
    let pct = Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : 0;
    if (pct > 0 && pct < 8 && elapsed > 20) {
      pct = Math.min(88, 8 + elapsed / 12);
    } else if (pct >= 85 && row.status === 'installing' && elapsed > 30) {
      pct = Math.min(98.5, pct + Math.floor(elapsed / 8) * 0.35);
    }
    const bar = document.getElementById('componentInstallProgressBar');
    const pctEl = document.getElementById('componentInstallProgressPct');
    const subEl = document.getElementById('componentInstallProgressSub');
    const logEl = document.getElementById('componentInstallProgressLog');
    if (bar) {
      bar.style.width = `${Math.max(4, pct)}%`;
      bar.classList.toggle('is-indeterminate', pct < 8 && elapsed < 20);
    }
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
    let step = String(row.message || '').trim();
    if (pct >= 90 && row.status === 'installing' && !/verifying|ready|finishing/i.test(step)) {
      step = 'Installing large packages in WSL — can take 10+ minutes';
    }
    setProgressText(step || info.progress);
    if (subEl) {
      subEl.textContent = elapsed
        ? `Running ${formatElapsed(elapsed)}. Large packages — this can take 10 minutes or more.`
        : 'Large packages — this can take 10 minutes or more. The bar updates as files download.';
    }
    if (logEl) {
      const line = String(row.log_line || '').trim();
      logEl.textContent = line && line !== step ? line : '';
    }
  }

  async function fetchInstallStatus(runtimeId) {
    const data = await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/install`, { timeoutMs: 12000 });
    const status = String(data?.status || (data?.installed ? 'installed' : 'idle'));
    return {
      installed: Boolean(data?.installed),
      status,
      error: String(data?.error || ''),
      progress: data?.progress,
      message: String(data?.message || ''),
      log_line: String(data?.log_line || ''),
      elapsed_s: Number(data?.elapsed_s || 0),
    };
  }

  async function startInstall(runtimeId) {
    const body = {};
    if (runtimeId === 'vllm') {
      body.backend = document.getElementById('componentInstallBackend')?.value || 'auto';
    }
    if (runtimeId === 'transformers') {
      body.torch_variant = document.getElementById('componentInstallTorch')?.value || 'auto';
    }
    if (runtimeId === 'freetoken') {
      body.backend = 'wsl';
    }
    await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      timeoutMs: 20000,
    });
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function waitForInstall(runtimeId, { onProgress } = {}) {
    const info = meta(runtimeId);
    for (let attempt = 0; attempt < 900; attempt += 1) {
      const row = await fetchInstallStatus(runtimeId);
      if (row.installed) return { ok: true };
      if (row.status === 'error') {
        return { ok: false, error: row.error || 'Install failed' };
      }
      if (row.status === 'installing') {
        renderInstallProgress(runtimeId, row);
        const pct = row.progress != null ? ` (${Math.round(Number(row.progress))}%)` : '';
        const line = `${row.message || info.progress}${pct}`;
        onProgress?.(line);
        window.DFlashStatusFeed?.setTransient?.(info.short + ' install', { secondary: line, ttlMs: 120000 });
      }
      await sleep(1500);
    }
    return { ok: false, error: 'Install timed out — check Settings → Downloads & engines' };
  }

  function showConfirm(runtimeId, { modelLabel = '' } = {}) {
    const info = meta(runtimeId);
    const titleEl = document.getElementById('componentInstallTitle');
    const messageEl = document.getElementById('componentInstallMessage');
    const subEl = document.getElementById('componentInstallSub');
    const kickerEl = document.getElementById('componentInstallKicker');
    const backendRow = document.getElementById('componentInstallBackendRow');
    const torchRow = document.getElementById('componentInstallTorchRow');

    if (kickerEl) kickerEl.textContent = 'Install required';
    if (titleEl) titleEl.textContent = `${info.short} is not installed`;
    if (messageEl) {
      messageEl.textContent = modelLabel
        ? `To load “${modelLabel}” with ${info.short}, ${info.short} must be installed first.`
        : `To use ${info.label}, it must be installed on this PC first.`;
    }
    if (subEl) subEl.textContent = info.note;
    if (backendRow) backendRow.classList.toggle('hidden', runtimeId !== 'vllm');
    if (torchRow) torchRow.classList.toggle('hidden', runtimeId !== 'transformers');

    setProgressVisible(false);
    modalMode = 'confirm';
    openModal();
  }

  function showProgress(runtimeId) {
    renderInstallProgress(runtimeId, { progress: 4, message: meta(runtimeId).progress, elapsed_s: 0 });
    setProgressVisible(true);
    modalMode = 'progress';
    openModal();
  }

  function confirmDialog(runtimeId, { modelLabel = '' } = {}) {
    return new Promise((resolve) => {
      showConfirm(runtimeId, { modelLabel });
      const modalEl = modal();
      const confirmBtn = document.getElementById('componentInstallConfirm');
      const cancelBtn = document.getElementById('componentInstallCancel');
      const closeBtn = modalEl?.querySelector('[data-action="close-component-install"]');
      const backdrop = modalEl?.querySelector('.lm-modal-backdrop');

      const cleanup = (result) => {
        cancelBtn?.removeEventListener('click', onCancel);
        confirmBtn?.removeEventListener('click', onConfirm);
        closeBtn?.removeEventListener('click', onCancel);
        backdrop?.removeEventListener('click', onBackdrop);
        resolve(result);
      };
      const onCancel = () => {
        closeModal();
        cleanup(false);
      };
      const onConfirm = () => cleanup(true);
      const onBackdrop = (e) => {
        if (e.target === backdrop) onCancel();
      };

      cancelBtn?.addEventListener('click', onCancel);
      confirmBtn?.addEventListener('click', onConfirm);
      closeBtn?.addEventListener('click', onCancel);
      backdrop?.addEventListener('click', onBackdrop);
    });
  }

  async function ensure(runtimeId, { modelLabel = '', silentProgress = false } = {}) {
    const rid = String(runtimeId || '').trim().toLowerCase();
    if (!ON_DEMAND.has(rid)) return true;

    try {
      let row = await fetchInstallStatus(rid);
      if (row.installed) return true;

      if (row.status === 'installing') {
        if (!silentProgress) showProgress(rid);
        const waited = await waitForInstall(rid);
        if (!silentProgress) closeModal();
        if (!waited.ok) {
          toast(waited.error || 'Install failed', false);
          return false;
        }
        return true;
      }

      const accepted = await confirmDialog(rid, { modelLabel });
      if (!accepted) return false;

      showProgress(rid);
      try {
        await startInstall(rid);
      } catch (err) {
        closeModal();
        toast(err.message || 'Could not start install', false);
        return false;
      }

      const waited = await waitForInstall(rid);
      closeModal();
      if (!waited.ok) {
        toast(waited.error || 'Install failed', false);
        return false;
      }
      toast(`${meta(rid).short} installed`);
      if (window.DFlashSettingsLive?.refreshComponentsPanel) {
        void window.DFlashSettingsLive.refreshComponentsPanel({ quiet: true });
      }
      return true;
    } catch (err) {
      closeModal();
      toast(err.message || 'Could not check install status', false);
      return false;
    }
  }

  function isOnDemand(runtimeId) {
    return ON_DEMAND.has(String(runtimeId || '').trim().toLowerCase());
  }

  window.DFlashComponentInstall = {
    ensure,
    isOnDemand,
    waitForInstall,
    ON_DEMAND,
  };
})();
