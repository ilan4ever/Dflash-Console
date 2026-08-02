/** Create DFlash stack wizard — pair target + accelerator and register engine profile */
(function () {
  const { api, toast } = window.ConsoleApi;

  const PROFILE_LABELS = {
    'gemma-chat': 'Gemma DFlash',
    'gemma-12-dflash': 'Gemma 12B DFlash',
    'qwen-dflash': 'Qwen DFlash',
    'bonsai-spec': 'Bonsai speculative',
  };

  let step = 1;
  let capableTargets = [];
  let shelved = false;
  let state = {
    targetPath: '',
    targetLabel: '',
    draftPath: '',
    draftLabel: '',
    matchData: null,
    targetNotice: '',
    label: '',
    serverId: '',
    modelId: '',
    profile: '',
    port: 8093,
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function modal() {
    return document.getElementById('stackWizardModal');
  }

  function isAcceleratorPath(path) {
    return /dflash|dspark/i.test(String(path || ''));
  }

  function syncDialogSize() {
    const main = document.querySelector('.df-workspace .lm-main');
    const dialog = document.querySelector('#stackWizardModal .df-stack-wizard-dialog');
    if (!main || !dialog) return;
    const rect = main.getBoundingClientRect();
    const width = Math.max(520, Math.floor(rect.width));
    const height = Math.max(420, Math.floor(rect.height * 0.9));
    dialog.style.width = `${width}px`;
    dialog.style.maxWidth = `${width}px`;
    dialog.style.height = `${height}px`;
    dialog.style.maxHeight = `${height}px`;
  }

  function openModal() {
    const el = modal();
    if (!el) return;
    shelved = false;
    syncDialogSize();
    el.classList.remove('is-shelved');
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    window.addEventListener('resize', syncDialogSize);
  }

  function shelfModal() {
    const el = modal();
    if (!el) return;
    shelved = true;
    el.classList.add('is-shelved');
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) {
      document.body.classList.remove('modal-open');
    }
  }

  function closeModal() {
    const el = modal();
    if (!el) return;
    shelved = false;
    hideCatalogReturn();
    el.classList.remove('open', 'is-shelved');
    el.setAttribute('aria-hidden', 'true');
    window.removeEventListener('resize', syncDialogSize);
    if (!document.querySelector('.lm-modal.open')) {
      document.body.classList.remove('modal-open');
    }
  }

  function showCatalogReturn(hint) {
    const bar = document.getElementById('stackWizardCatalogReturn');
    const hintEl = document.getElementById('stackWizardCatalogReturnHint');
    if (hintEl) hintEl.textContent = hint || '';
    bar?.classList.remove('hidden');
  }

  function hideCatalogReturn() {
    document.getElementById('stackWizardCatalogReturn')?.classList.add('hidden');
  }

  function formatSize(sizeGb) {
    if (sizeGb == null) return '';
    const gb = Number(sizeGb);
    if (!gb || Number.isNaN(gb)) return '';
    if (gb < 0.01) return `${Math.max(1, Math.round(gb * 1024))} MB`;
    return `${gb} GB`;
  }

  function samePath(left, right) {
    return String(left || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
      === String(right || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
  }

  function targetOptionsHtml(selectedPath = '') {
    if (!capableTargets.length) {
      return '<option value="">No DFlash-ready targets on this PC yet</option>';
    }
    const sorted = capableTargets.slice().sort((a, b) => String(a.label || a.filename || '').localeCompare(String(b.label || b.filename || ''), undefined, { sensitivity: 'base' }));
    return ['<option value="">Choose a DFlash-ready target…</option>']
      .concat(sorted.map((model) => {
        const size = model.size_gb != null ? ` · ${formatSize(model.size_gb)}` : '';
        const accel = model.best_accelerator ? ` · pairs with ${model.best_accelerator}` : '';
        const label = `${model.label || model.filename}${size}${accel}`;
        const selected = samePath(model.path, selectedPath) ? ' selected' : '';
        return `<option value="${escapeHtml(model.path)}"${selected}>${escapeHtml(label)}</option>`;
      }))
      .join('');
  }

  function acceleratorCardsHtml() {
    const local = state.matchData?.local_accelerators || [];
    const hf = state.matchData?.hf_suggestions || [];
    const parts = [];

    if (local.length) {
      parts.push('<div class="df-stack-section-label">On this PC</div>');
      parts.push('<div class="df-stack-pick-list">');
      local.forEach((row) => {
        const active = state.draftPath === row.path ? ' active' : '';
        const size = row.size_gb != null ? formatSize(row.size_gb) : '';
        parts.push(`
          <button type="button" class="df-stack-pick${active}" data-pick-accel="${escapeHtml(row.path)}" data-pick-label="${escapeHtml(row.filename)}">
            <span class="df-stack-pick-title">${escapeHtml(row.filename)}</span>
            <span class="df-stack-pick-meta">${escapeHtml(row.publisher || 'local')}${size ? ` · ${escapeHtml(size)}` : ''} · match ${escapeHtml(String(row.score || ''))}</span>
          </button>`);
      });
      parts.push('</div>');
    } else {
      parts.push('<p class="lm-setting-desc">No matching DFlash accelerator found locally. Search Hugging Face below, download one, then use <strong>Back to Create DFlash stack</strong> at the top.</p>');
    }

    if (hf.length) {
      parts.push('<div class="df-stack-section-label">Suggested on Hugging Face</div>');
      parts.push('<div class="df-stack-pick-list df-stack-pick-list-hf">');
      hf.forEach((row) => {
        parts.push(`
          <button type="button" class="df-stack-pick df-stack-pick-hf" data-hf-repo="${escapeHtml(row.id || '')}">
            <span class="df-stack-pick-title">${escapeHtml(row.title || row.id || 'Model')}</span>
            <span class="df-stack-pick-meta">${escapeHtml(row.author || 'HF')}${row.size_label ? ` · ${escapeHtml(row.size_label)}` : ''}</span>
          </button>`);
      });
      parts.push('</div>');
    }

    parts.push(`
      <div class="df-stack-hf-actions">
        <button class="lm-btn ghost small" type="button" id="stackWizardSearchHf">Search Hugging Face for more accelerators</button>
      </div>`);

    return parts.join('');
  }

  function reviewHtml() {
    const profileLabel = PROFILE_LABELS[state.profile] || state.profile || '—';
    return `
      <div class="df-stack-review">
        <div class="df-stack-review-row"><span>Stack name</span><strong>${escapeHtml(state.label || '—')}</strong></div>
        <div class="df-stack-review-row"><span>Target model</span><code>${escapeHtml(state.targetLabel || state.targetPath || '—')}</code></div>
        <div class="df-stack-review-row"><span>DFlash accelerator</span><code>${escapeHtml(state.draftLabel || state.draftPath || '—')}</code></div>
        <div class="df-stack-review-row"><span>Engine profile</span><strong>${escapeHtml(profileLabel)}</strong></div>
        <div class="df-stack-review-row"><span>API port</span><strong>${escapeHtml(String(state.port || '—'))}</strong></div>
      </div>
      <p class="lm-setting-desc">Creates a runnable DFlash stack in your model library. You can load it onto the GPU afterward.</p>`;
  }

  function stepTitle() {
    if (step === 1) return 'Choose DFlash-ready target';
    if (step === 2) return 'Choose DFlash accelerator';
    return 'Review and create';
  }

  function canContinue() {
    if (step === 1) return !!state.targetPath;
    if (step === 2) return !!state.draftPath;
    return !!(state.label && state.targetPath && state.draftPath && state.port);
  }

  function render() {
    const title = document.getElementById('stackWizardTitle');
    const subtitle = document.getElementById('stackWizardSubtitle');
    const body = document.getElementById('stackWizardBody');
    const backBtn = document.getElementById('stackWizardBack');
    const nextBtn = document.getElementById('stackWizardNext');
    const steps = document.getElementById('stackWizardSteps');
    if (!body) return;

    if (title) title.textContent = 'Create DFlash stack';
    if (subtitle) subtitle.textContent = stepTitle();
    if (steps) {
      steps.innerHTML = [1, 2, 3].map((n) =>
        `<span class="df-stack-step${n === step ? ' active' : ''}${n < step ? ' done' : ''}">${n}</span>`,
      ).join('');
    }

    if (step === 1) {
      const emptyHint = capableTargets.length
        ? 'A DFlash stack needs two files: a full target GGUF and a matching DFlash or DSpark accelerator.'
        : 'No compatible target is ready yet. Download a DFlash accelerator from Model catalog, then return here.';
      const targetNotice = state.targetNotice
        ? `<div class="df-stack-preflight is-unavailable"><strong>This model cannot be used yet.</strong><span>${escapeHtml(state.targetNotice)}</span></div>`
        : '';
      body.innerHTML = `
        <p class="lm-setting-desc">${escapeHtml(emptyHint)}</p>
        ${targetNotice}
        <label class="df-stack-field-label" for="stackWizardTargetPick">Target model</label>
        <select class="lm-select" id="stackWizardTargetPick"${capableTargets.length ? '' : ' disabled'}>${targetOptionsHtml(state.targetPath)}</select>
        ${capableTargets.length
          ? '<p class="lm-setting-desc">Only targets with a compatible accelerator on this PC are listed.</p>'
          : '<p class="df-stack-empty-actions"><button type="button" class="lm-btn ghost small" id="stackWizardBrowseAccel">Browse DFlash accelerators on Hugging Face</button></p>'}`;
      document.getElementById('stackWizardTargetPick')?.addEventListener('change', (event) => {
        const path = event.target.value || '';
        const model = capableTargets.find((row) => samePath(row.path, path));
        state.targetPath = path;
        state.targetLabel = model?.label || model?.filename || path.split(/[/\\]/).pop() || '';
        state.matchData = null;
        state.draftPath = '';
        state.draftLabel = '';
        if (nextBtn) nextBtn.disabled = !canContinue();
      });
      document.getElementById('stackWizardBrowseAccel')?.addEventListener('click', () => {
        openHfCatalog('dflash gguf', { forAccelerators: true });
      });
    } else if (step === 2) {
      body.innerHTML = `
        <p class="lm-setting-desc">Target: <strong>${escapeHtml(state.targetLabel || state.targetPath)}</strong></p>
        ${acceleratorCardsHtml()}`;
      body.querySelectorAll('[data-pick-accel]').forEach((btn) => {
        btn.addEventListener('click', () => {
          state.draftPath = btn.dataset.pickAccel || '';
          state.draftLabel = btn.dataset.pickLabel || state.draftPath.split(/[/\\]/).pop() || '';
          render();
        });
      });
      body.querySelectorAll('[data-hf-repo]').forEach((btn) => {
        btn.addEventListener('click', () => {
          openHfCatalog(state.matchData?.hf_query || `${state.targetLabel} DFlash gguf`);
        });
      });
      document.getElementById('stackWizardSearchHf')?.addEventListener('click', () => {
        openHfCatalog(state.matchData?.hf_query || `${state.targetLabel} DFlash gguf`);
      });
    } else {
      body.innerHTML = `
        <label class="df-stack-field-label" for="stackWizardLabel">Stack name</label>
        <input class="lm-search-input" id="stackWizardLabel" value="${escapeHtml(state.label || '')}" maxlength="120">
        <label class="df-stack-field-label" for="stackWizardPort">API port</label>
        <input class="lm-num" id="stackWizardPort" type="number" min="1024" max="65535" value="${escapeHtml(String(state.port || ''))}">
        ${reviewHtml()}`;
      document.getElementById('stackWizardLabel')?.addEventListener('input', (event) => {
        state.label = event.target.value.trim();
        if (nextBtn) nextBtn.disabled = !canContinue();
      });
      document.getElementById('stackWizardPort')?.addEventListener('input', (event) => {
        state.port = Number(event.target.value) || state.port;
        if (nextBtn) nextBtn.disabled = !canContinue();
      });
    }

    if (backBtn) backBtn.disabled = step === 1;
    if (nextBtn) {
      nextBtn.textContent = step === 3 ? 'Create stack' : 'Continue';
      nextBtn.disabled = !canContinue();
    }
  }

  function cleanSearchQuery(query) {
    return String(query || 'dflash gguf').replace(/["']/g, '').trim();
  }

  function openHfCatalog(query, { forAccelerators = false } = {}) {
    const q = cleanSearchQuery(query);
    shelfModal();
    showCatalogReturn(
      forAccelerators
        ? 'Find and download a DFlash accelerator, then return to the wizard.'
        : `Find a DFlash accelerator for ${state.targetLabel || 'your target'}, download it, then return here.`,
    );
    window.DFlashShell?.setView?.('catalog');
    const input = document.getElementById('hfSearchInput');
    const category = document.getElementById('hfSearchCategory');
    if (category) category.value = 'dflash';
    if (input) input.value = q;
    void window.DFlashModelSearchLive?.runSearch?.();
  }

  async function resumeFromCatalog() {
    hideCatalogReturn();
    window.DFlashShell?.setView?.('models');
    try {
      await loadCapableTargets();
      if (state.targetPath) {
        await loadMatchData();
        if (state.draftPath || state.matchData?.local_accelerators?.length) {
          const best = state.matchData.local_accelerators[0];
          if (!state.draftPath && best?.path) {
            state.draftPath = best.path;
            state.draftLabel = best.filename || '';
          }
          step = 2;
        }
      }
      openModal();
      render();
      requestAnimationFrame(syncDialogSize);
    } catch (err) {
      toast(err.message || 'Could not resume wizard', false);
    }
  }

  async function loadCapableTargets() {
    const data = await api('/api/stacks/capable-targets');
    capableTargets = data.targets || [];
  }

  async function loadMatchData() {
    if (!state.targetPath) return;
    state.matchData = await api(`/api/stacks/match?target_path=${encodeURIComponent(state.targetPath)}`);
    if (!state.label) state.label = state.matchData.suggested_label || state.targetLabel;
    if (!state.serverId) state.serverId = state.matchData.suggested_server_id || '';
    if (!state.modelId) state.modelId = state.matchData.suggested_model_id || '';
    if (!state.profile) state.profile = state.matchData.suggested_profile || 'qwen-dflash';
    if (!state.port) state.port = state.matchData.suggested_port || 8093;
    if (!state.draftPath && state.matchData.local_accelerators?.length) {
      const best = state.matchData.local_accelerators[0];
      state.draftPath = best.path || '';
      state.draftLabel = best.filename || '';
    }
  }

  async function createStack() {
    const payload = {
      label: state.label,
      target_path: state.targetPath,
      draft_path: state.draftPath,
      profile: state.profile,
      port: state.port,
      model_id: state.modelId,
      id: state.serverId || undefined,
    };
    const result = await api('/api/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    closeModal();
    toast(`Created ${result.server?.label || 'DFlash stack'}`);
    await window.DFlashModelsLive?.refresh?.();
    await window.DFlashServerLive?.refresh?.();
    const serverId = result.server?.id;
    if (serverId) {
      window.DFlashModelsLive?.setTypeFilter?.('dflash');
      await window.DFlashModelsLive?.selectModel?.(serverId);
    }
    return result;
  }

  async function goNext() {
    if (step === 1) {
      if (!state.targetPath) return;
      await loadMatchData();
      if (!state.matchData?.local_accelerators?.length) {
        step = 2;
        render();
        return;
      }
      step = 2;
      render();
      return;
    }
    if (step === 2) {
      if (!state.draftPath) return;
      if (!state.label) await loadMatchData();
      step = 3;
      render();
      return;
    }
    try {
      await createStack();
    } catch (err) {
      toast(err.message || 'Could not create stack', false);
    }
  }

  function goBack() {
    if (step <= 1) return;
    step -= 1;
    render();
  }

  async function open(options = {}) {
    step = 1;
    hideCatalogReturn();
    state = {
      targetPath: options.targetPath || '',
      targetLabel: options.targetLabel || '',
      draftPath: options.draftPath || '',
      draftLabel: options.draftLabel || '',
      matchData: null,
      targetNotice: '',
      label: options.label || '',
      serverId: '',
      modelId: '',
      profile: '',
      port: 0,
    };

    try {
      await loadCapableTargets();

      if (options.targetPath) {
        const inList = capableTargets.some((row) => samePath(row.path, options.targetPath));
        if (!inList && !isAcceleratorPath(options.draftPath)) {
          state.targetNotice = 'No compatible DFlash accelerator was found on this PC. Download one first, then reopen the wizard.';
          state.targetPath = '';
          state.targetLabel = '';
        } else if (!state.targetLabel) {
          const model = capableTargets.find((row) => samePath(row.path, options.targetPath));
          state.targetLabel = model?.label || model?.filename || options.targetPath.split(/[/\\]/).pop() || '';
        }
      }
      if (options.draftPath && !state.draftLabel) {
        state.draftLabel = options.draftPath.split(/[/\\]/).pop() || '';
      }

      if (state.targetPath && state.draftPath) {
        await loadMatchData();
        step = 3;
      } else if (state.targetPath) {
        await loadMatchData();
        step = 2;
      } else if (options.draftPath) {
        step = 1;
      }

      openModal();
      render();
      requestAnimationFrame(syncDialogSize);
    } catch (err) {
      toast(err.message || 'Could not open stack wizard', false);
    }
  }

  function bind() {
    document.getElementById('stackWizardOpenBtn')?.addEventListener('click', () => {
      void open();
    });
    document.getElementById('stackWizardCancel')?.addEventListener('click', closeModal);
    document.getElementById('stackWizardBack')?.addEventListener('click', () => goBack());
    document.getElementById('stackWizardNext')?.addEventListener('click', () => {
      void goNext();
    });
    document.getElementById('stackWizardCatalogReturnBtn')?.addEventListener('click', () => {
      void resumeFromCatalog();
    });
    document.querySelector('#stackWizardModal [data-action="close-modal"]')?.addEventListener('click', closeModal);
    document.querySelector('#stackWizardModal .lm-modal-backdrop')?.addEventListener('click', (event) => {
      if (event.target === event.currentTarget) closeModal();
    });
  }

  bind();
  window.DFlashStackWizard = { open, close: closeModal, resumeFromCatalog };
})();
