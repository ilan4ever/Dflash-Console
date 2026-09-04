/** Create DFlash stack wizard — pair target + accelerator and register engine profile */
(function () {
  const { api, toast } = window.ConsoleApi;

  const PROFILE_LABELS = {
    'gemma-chat': 'Gemma DFlash',
    'gemma-12-dflash': 'Gemma 12B DFlash',
    'qwen-dflash': 'Qwen DFlash',
    'bonsai-spec': 'Bonsai speculative',
  };

  let mode = 'create';
  let step = 1;
  let capableTargets = [];
  let shelved = false;
  let downloadWatch = null;
  let state = {
    targetPath: '',
    targetLabel: '',
    draftPath: '',
    draftLabel: '',
    currentDraftPath: '',
    currentDraftLabel: '',
    serverId: '',
    stackLabel: '',
    matchData: null,
    targetNotice: '',
    label: '',
    serverIdNew: '',
    modelId: '',
    profile: '',
    port: 8093,
    dflashGeneration: 'dflash1',
    generationTouched: false,
    knownAcceleratorPaths: new Set(),
    awaitingCatalog: false,
    onAttached: null,
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

  function pathKey(path) {
    return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
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
    stopReplaceDownloadWatch();
    state.awaitingCatalog = false;
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
    const btn = document.getElementById('stackWizardCatalogReturnBtn');
    if (btn) {
      btn.textContent = mode === 'replace'
        ? '← Back to Check & replace accelerator'
        : '← Back to Create DFlash stack';
    }
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
    return pathKey(left) === pathKey(right);
  }

  function rememberAccelerators(rows) {
    (rows || []).forEach((row) => {
      const key = pathKey(row?.path);
      if (key) state.knownAcceleratorPaths.add(key);
    });
    if (state.currentDraftPath) state.knownAcceleratorPaths.add(pathKey(state.currentDraftPath));
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

  function generationToggleHtml() {
    const gen1 = state.dflashGeneration === 'dflash1' ? ' active' : '';
    const gen2 = state.dflashGeneration === 'dflash2' ? ' active' : '';
    const recommended = state.matchData?.recommended_generation || '';
    const recLabel = state.matchData?.recommended_generation_label || '';
    const recReasons = state.matchData?.generation_recommendation_reasons || [];
    const recHint = recLabel
      ? `<p class="lm-setting-desc df-stack-generation-rec">Recommended: <strong>${escapeHtml(recLabel)}</strong>${recReasons.length ? ` — ${escapeHtml(recReasons.slice(0, 2).join(' · '))}` : ''}</p>`
      : '';
    const badge1 = recommended === 'dflash1' ? ' <span class="df-stack-gen-rec">Recommended</span>' : '';
    const badge2 = recommended === 'dflash2' ? ' <span class="df-stack-gen-rec">Recommended</span>' : '';
    const replaceHint = mode === 'replace'
      ? '<p class="lm-setting-desc">Switch to <strong>DFlash 2</strong> to search for DFlash2 drafters (for example <code>Qwen3.8-27B-DFlash2</code>). DFlash 1 only finds first-generation accelerators like the one you already installed.</p>'
      : '<p class="lm-setting-desc">The app pre-selects the generation with the best accelerator match for your target. You can switch anytime.</p>';
    return `
      <div class="df-stack-generation-toggle" role="group" aria-label="Accelerator generation">
        <span class="df-stack-section-label">Accelerator generation</span>
        ${recHint}
        <div class="df-stack-generation-buttons">
          <button type="button" class="lm-btn ghost small df-stack-gen-btn${gen1}" data-stack-gen="dflash1">DFlash 1${badge1}</button>
          <button type="button" class="lm-btn ghost small df-stack-gen-btn${gen2}" data-stack-gen="dflash2">DFlash 2${badge2}</button>
        </div>
        ${replaceHint}
      </div>`;
  }

  function bindGenerationToggle(root) {
    root.querySelectorAll('[data-stack-gen]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const next = btn.dataset.stackGen || 'dflash1';
        if (next === state.dflashGeneration) return;
        state.generationTouched = true;
        state.dflashGeneration = next;
        state.draftPath = '';
        state.draftLabel = '';
        void loadMatchData().then(() => render());
      });
    });
  }

  function acceleratorBadge(row) {
    if (row.is_current) return ' · current';
    if (row.better_than_current) return ' · better match';
    if (row.is_recommended) return ' · recommended';
    return '';
  }

  function hfCardMeta(row) {
    const parts = [];
    const genLabel = row.dflash_generation_label || '';
    if (genLabel) parts.push(genLabel);
    if (row.author) parts.push(String(row.author));
    if (row.size_label) parts.push(String(row.size_label));
    if (row.match_score != null) parts.push(`match ${row.match_score}`);
    if (row.downloads_label) parts.push(`${row.downloads_label} downloads`);
    else if (row.downloads) parts.push(`${row.downloads} downloads`);
    if (row.updated_ago) parts.push(`updated ${row.updated_ago}`);
    if (row.local_loadable) parts.push('installed · ready');
    else if (row.local_ready) parts.push('installed · needs file');
    return parts.join(' · ');
  }

  function hfCardReason(row) {
    const reasons = Array.isArray(row.recommendation_reasons) ? row.recommendation_reasons : [];
    if (!reasons.length) return '';
    return `<span class="df-stack-pick-reason">${escapeHtml(reasons.slice(0, 3).join(' · '))}</span>`;
  }

  function acceleratorCardsHtml({ replaceMode = false } = {}) {
    const local = state.matchData?.local_accelerators || [];
    const hf = state.matchData?.hf_suggestions || [];
    const current = state.matchData?.current_draft;
    const parts = [];

    if (replaceMode && current) {
      parts.push('<div class="df-stack-section-label">Current accelerator</div>');
      parts.push(`
        <div class="df-stack-pick active is-current" aria-current="true">
          <span class="df-stack-pick-title">${escapeHtml(current.filename || state.currentDraftLabel || 'Current')}</span>
          <span class="df-stack-pick-meta">match ${escapeHtml(String(current.score ?? state.matchData?.current_score ?? ''))}</span>
        </div>`);
    }

    const localRows = replaceMode
      ? local.filter((row) => !row.is_current)
      : local;

    if (localRows.length) {
      parts.push(`<div class="df-stack-section-label">${replaceMode ? 'Other accelerators on this PC' : 'On this PC'}</div>`);
      parts.push('<div class="df-stack-pick-list">');
      localRows.forEach((row) => {
        const active = state.draftPath === row.path ? ' active' : '';
        const size = row.size_gb != null ? formatSize(row.size_gb) : '';
        const badge = acceleratorBadge(row);
        const genLabel = row.dflash_generation_label || window.DFlashModelGroups?.acceleratorGenerationLabel?.(row) || '';
        const genBadge = genLabel ? ` · ${genLabel}` : '';
        parts.push(`
          <button type="button" class="df-stack-pick${active}${row.better_than_current ? ' is-better' : ''}" data-pick-accel="${escapeHtml(row.path)}" data-pick-label="${escapeHtml(row.filename)}">
            <span class="df-stack-pick-title">${escapeHtml(row.filename)}</span>
            <span class="df-stack-pick-meta">${escapeHtml(row.publisher || 'local')}${size ? ` · ${escapeHtml(size)}` : ''}${escapeHtml(genBadge)} · match ${escapeHtml(String(row.score || ''))}${escapeHtml(badge)}</span>
          </button>`);
      });
      parts.push('</div>');
    } else if (!replaceMode) {
      parts.push('<p class="lm-setting-desc">No matching accelerator is installed for this target and generation. Search Hugging Face below or switch to DFlash 1.</p>');
    }

    if (hf.length) {
      const recommended = state.matchData?.recommended_hf;
      parts.push('<div class="df-stack-section-label">Matching accelerators on Hugging Face</div>');
      if (recommended?.id) {
        parts.push(
          `<p class="lm-setting-desc df-stack-recommended-hint">Recommended: <strong>${escapeHtml(recommended.author || recommended.id)}</strong> — ${escapeHtml((recommended.recommendation_reasons || []).slice(0, 2).join(' · ') || 'best match for this target')}. Click it to open in Model catalog and download.</p>`,
        );
      }
      parts.push('<div class="df-stack-pick-list df-stack-pick-list-hf">');
      hf.forEach((row) => {
        const recommendedClass = row.is_recommended ? ' is-recommended' : '';
        const badge = row.is_recommended ? '<span class="df-stack-pick-badge">Recommended</span>' : '';
        parts.push(`
          <button type="button" class="df-stack-pick df-stack-pick-hf${recommendedClass}" data-hf-repo="${escapeHtml(row.id || '')}">
            <span class="df-stack-pick-head">
              ${badge}
              <span class="df-stack-pick-title">${escapeHtml(row.title || row.id || 'Model')}</span>
            </span>
            <span class="df-stack-pick-meta">${escapeHtml(hfCardMeta(row))}</span>
            ${hfCardReason(row)}
          </button>`);
      });
      parts.push('</div>');
    }

    parts.push(`
      <div class="df-stack-hf-actions">
        <button class="lm-btn ghost small" type="button" id="stackWizardSearchHf">${replaceMode ? 'Browse Model catalog for more accelerators' : 'Search Hugging Face for matching accelerators'}</button>
      </div>`);

    return parts.join('');
  }

  function replaceSummaryHtml() {
    const better = state.matchData?.has_better_local;
    const hfCount = (state.matchData?.hf_suggestions || []).length;
    const generation = state.dflashGeneration === 'dflash2' ? 'DFlash 2' : 'DFlash 1';
    let hint = 'Compare your current draft with other local files and Hugging Face results.';
    if (better) hint = 'A stronger local match is available — pick it below or search Hugging Face for a newer accelerator.';
    else if (state.dflashGeneration === 'dflash1' && !better) {
      hint = 'Your current accelerator is already the best installed DFlash 1 match. To upgrade to DFlash 2, switch generation below, then browse Model catalog.';
    } else if (hfCount) {
      const rec = state.matchData?.recommended_hf;
      if (rec?.author || rec?.id) {
        hint = `Recommended download: ${rec.author || rec.id}. Pick it below or browse Model catalog — it connects automatically when the download finishes.`;
      } else {
        hint = `No stronger local ${generation} match yet. Browse Model catalog to download one — it will connect automatically when the download finishes.`;
      }
    }
    return `
      <p class="lm-setting-desc">Stack: <strong>${escapeHtml(state.stackLabel || state.targetLabel || state.targetPath)}</strong></p>
      <p class="lm-setting-desc">${escapeHtml(hint)}</p>`;
  }

  function reviewHtml() {
    const profileLabel = PROFILE_LABELS[state.profile] || state.profile || '—';
    const copyMode = state.copyMode || 'copy';
    const seg = (value, label, desc) => `
      <label class="df-stack-copy-option${copyMode === value ? ' active' : ''}">
        <input type="radio" name="stackWizardCopyMode" value="${value}" ${copyMode === value ? 'checked' : ''}>
        <span class="df-stack-copy-option-title">${escapeHtml(label)}</span>
        <span class="df-stack-copy-option-desc">${escapeHtml(desc)}</span>
      </label>`;
    return `
      <div class="df-stack-review">
        <div class="df-stack-review-row"><span>Stack name</span><strong>${escapeHtml(state.label || '—')}</strong></div>
        <div class="df-stack-review-row"><span>Target model</span><code>${escapeHtml(state.targetLabel || state.targetPath || '—')}</code></div>
        <div class="df-stack-review-row"><span>Accelerator generation</span><strong>${escapeHtml(state.dflashGeneration === 'dflash2' ? 'DFlash 2' : 'DFlash 1')}</strong></div>
        <div class="df-stack-review-row"><span>DFlash accelerator</span><code>${escapeHtml(state.draftLabel || state.draftPath || '—')}</code></div>
        <div class="df-stack-review-row"><span>Engine profile</span><strong>${escapeHtml(profileLabel)}</strong></div>
        <div class="df-stack-review-row"><span>API port</span><strong>${escapeHtml(String(state.port || '—'))}</strong></div>
      </div>
      <div class="df-stack-copy-mode" id="stackWizardCopyModeGroup">
        <div class="df-stack-section-label">Bring into DFlash Console library?</div>
        ${seg('copy', 'Copy into DFlash Console', 'Copies target + accelerator into your Console folder, keeps the originals.')}
        ${seg('move', 'Move into DFlash Console', 'Moves target + accelerator into your Console folder and removes the originals for full control.')}
        ${seg('none', 'Don’t copy — use as-is', 'Loads the files from their current location; nothing is copied or moved.')}
      </div>
      <p class="lm-setting-desc">Creates a runnable DFlash stack in your model library. You can load it onto the GPU afterward.</p>`;
  }

  function stepTitle() {
    if (mode === 'replace') return 'Check & replace accelerator';
    if (step === 1) return 'Choose DFlash-ready target';
    if (step === 2) return 'Choose DFlash 1 or DFlash 2 accelerator';
    return 'Review and create';
  }

  function canContinue() {
    if (mode === 'replace') {
      return !!state.draftPath && !samePath(state.draftPath, state.currentDraftPath);
    }
    if (step === 1) return !!state.targetPath;
    if (step === 2) return !!state.draftPath;
    return !!(state.label && state.targetPath && state.draftPath && state.port);
  }

  function bindAcceleratorPickers(root) {
    root.querySelectorAll('[data-pick-accel]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.draftPath = btn.dataset.pickAccel || '';
        state.draftLabel = btn.dataset.pickLabel || state.draftPath.split(/[/\\]/).pop() || '';
        render();
      });
    });
    root.querySelectorAll('[data-hf-repo]').forEach((btn) => {
      btn.addEventListener('click', () => {
        openHfCatalog(state.matchData?.hf_query || `${state.targetLabel} DFlash gguf`, {
          revealRepo: btn.dataset.hfRepo || '',
        });
      });
    });
    root.querySelector('#stackWizardSearchHf')?.addEventListener('click', () => {
      openHfCatalog(state.matchData?.hf_query || `${state.targetLabel} DFlash gguf`);
    });
  }

  function render() {
    const title = document.getElementById('stackWizardTitle');
    const subtitle = document.getElementById('stackWizardSubtitle');
    const body = document.getElementById('stackWizardBody');
    const backBtn = document.getElementById('stackWizardBack');
    const nextBtn = document.getElementById('stackWizardNext');
    const steps = document.getElementById('stackWizardSteps');
    if (!body) return;

    if (title) title.textContent = mode === 'replace' ? 'Check & replace accelerator' : 'Create DFlash stack';
    if (subtitle) subtitle.textContent = stepTitle();
    if (steps) {
      if (mode === 'replace') {
        steps.innerHTML = '<span class="df-stack-step active">1</span>';
      } else {
        steps.innerHTML = [1, 2, 3].map((n) =>
          `<span class="df-stack-step${n === step ? ' active' : ''}${n < step ? ' done' : ''}">${n}</span>`,
        ).join('');
      }
    }

    if (mode === 'replace') {
      body.innerHTML = `
        ${replaceSummaryHtml()}
        ${generationToggleHtml()}
        ${acceleratorCardsHtml({ replaceMode: true })}`;
      bindGenerationToggle(body);
      bindAcceleratorPickers(body);
    } else if (step === 1) {
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
        <select class="lm-select" id="stackWizardTargetPick" data-df-model-filter="1"${capableTargets.length ? '' : ' disabled'}>${targetOptionsHtml(state.targetPath)}</select>
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
        ${generationToggleHtml()}
        ${acceleratorCardsHtml()}`;
      bindGenerationToggle(body);
      bindAcceleratorPickers(body);
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
      body.querySelectorAll('input[name="stackWizardCopyMode"]').forEach((radio) => {
        radio.addEventListener('change', (event) => {
          if (event.target.checked) state.copyMode = event.target.value;
          render();
        });
      });
    }

    if (backBtn) backBtn.classList.toggle('hidden', mode === 'replace' || step === 1);
    if (nextBtn) {
      nextBtn.textContent = mode === 'replace'
        ? 'Replace accelerator'
        : (step === 3 ? 'Create stack' : 'Continue');
      nextBtn.disabled = !canContinue();
    }
  }

  function cleanSearchQuery(query) {
    return String(query || 'dflash gguf').replace(/["']/g, '').trim();
  }

  function startReplaceDownloadWatch() {
    if (downloadWatch || mode !== 'replace') return;
    downloadWatch = window.DFlashDownloadQueue?.subscribe?.((jobs) => {
      if (mode !== 'replace' || !state.awaitingCatalog) return;
      const done = (jobs || []).find((job) => {
        if (job.status !== 'done') return false;
        const file = String(job.filename || job.path || '');
        return isAcceleratorPath(file);
      });
      if (!done?.path) return;
      const key = pathKey(done.path);
      if (!key || state.knownAcceleratorPaths.has(key)) return;
      state.awaitingCatalog = false;
      stopReplaceDownloadWatch();
      void applyReplaceDraft(done.path, done.filename || done.path.split(/[/\\]/).pop());
    });
  }

  function stopReplaceDownloadWatch() {
    if (typeof downloadWatch === 'function') {
      downloadWatch();
      downloadWatch = null;
    }
  }

  function openHfCatalog(query, { forAccelerators = false, revealRepo = '' } = {}) {
    const q = cleanSearchQuery(query);
    shelfModal();
    if (mode === 'replace') {
      state.awaitingCatalog = true;
      startReplaceDownloadWatch();
      showCatalogReturn(
        `Download a better accelerator for ${state.stackLabel || state.targetLabel || 'this stack'}. It will connect automatically when the download finishes — or click here to pick a local file instead.`,
      );
    } else {
      showCatalogReturn(
        forAccelerators
          ? 'Find and download a DFlash accelerator, then return to the wizard.'
          : `Find a DFlash accelerator for ${state.targetLabel || 'your target'}, download it, then return here.`,
      );
    }
    window.DFlashShell?.setView?.('catalog');
    const input = document.getElementById('hfSearchInput');
    const category = document.getElementById('hfSearchCategory');
    const sort = document.getElementById('hfSearchSort');
    if (category) category.value = state.dflashGeneration === 'dflash2' ? 'dflash2' : 'dflash';
    if (sort) sort.value = 'accelerators';
    if (input) input.value = q;
    void window.DFlashModelSearchLive?.runSearch?.().then(() => {
      if (revealRepo) window.DFlashModelSearchLive?.revealRepo?.(revealRepo, { preferCache: true, backgroundDetail: false });
    });
  }

  async function applyReplaceDraft(draftPath, draftLabel) {
    if (!state.serverId || !draftPath) return;
    if (samePath(draftPath, state.currentDraftPath)) {
      closeModal();
      toast('Already using that accelerator');
      return;
    }
    try {
      const result = await api(`/api/stacks/${encodeURIComponent(state.serverId)}/replace-draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft_path: draftPath }),
      });
      const onAttached = state.onAttached;
      closeModal();
      hideCatalogReturn();
      if (result?.unchanged) {
        toast('Already using that accelerator');
      } else {
        toast(`Connected ${draftLabel || 'accelerator'} to ${state.stackLabel || state.targetLabel || 'stack'}`);
      }
      await window.DFlashModelsLive?.refresh?.({ rebindInspector: true });
      await window.DFlashServerLive?.refresh?.();
      if (state.serverId) {
        window.DFlashModelsLive?.setTypeFilter?.('dflash');
        await window.DFlashModelsLive?.selectModel?.(state.serverId);
      }
      if (typeof onAttached === 'function') {
        await onAttached(result);
      }
    } catch (err) {
      toast(err.message || 'Could not replace accelerator', false);
    }
  }

  function pickResumeAccelerator() {
    const local = state.matchData?.local_accelerators || [];
    const better = local.find((row) => row.better_than_current && !row.is_current);
    if (better?.path) return better;
    return local.find((row) => {
      if (row.is_current) return false;
      const key = pathKey(row.path);
      return key && !state.knownAcceleratorPaths.has(key);
    }) || null;
  }

  async function resumeFromCatalog() {
    hideCatalogReturn();
    window.DFlashShell?.setView?.('models');
    if (mode === 'replace') {
      stopReplaceDownloadWatch();
      state.awaitingCatalog = false;
      try {
        await loadMatchData();
        const pick = pickResumeAccelerator();
        if (pick?.path) {
          await applyReplaceDraft(pick.path, pick.filename);
          return;
        }
        openModal();
        render();
        requestAnimationFrame(syncDialogSize);
        toast('Pick an accelerator or download one from Model catalog', false);
      } catch (err) {
        toast(err.message || 'Could not resume', false);
      }
      return;
    }

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

  function applyGenerationFromMatch(matchData) {
    if (!matchData || state.generationTouched) return;
    const next = String(matchData.recommended_generation || matchData.dflash_generation || 'dflash1');
    state.dflashGeneration = next === 'dflash2' ? 'dflash2' : 'dflash1';
  }

  async function loadMatchData() {
    if (!state.targetPath) return;
    const requestedGen = state.generationTouched
      ? (state.dflashGeneration || 'dflash1')
      : 'auto';
    let url = `/api/stacks/match?target_path=${encodeURIComponent(state.targetPath)}&dflash_generation=${encodeURIComponent(requestedGen)}`;
    if (state.currentDraftPath) {
      url += `&current_draft=${encodeURIComponent(state.currentDraftPath)}`;
    }
    // Matching may query Hugging Face in addition to scanning local files.
    // Give the repair wizard the same generous window as the model catalog
    // instead of letting the generic eight-second UI timeout close the flow.
    state.matchData = await api(url, { timeoutMs: 60000 });
    applyGenerationFromMatch(state.matchData);
    rememberAccelerators(state.matchData?.local_accelerators || []);
    if (!state.label) state.label = state.matchData.suggested_label || state.targetLabel;
    if (!state.serverIdNew) state.serverIdNew = state.matchData.suggested_server_id || '';
    if (!state.modelId) state.modelId = state.matchData.suggested_model_id || '';
    if (!state.profile) state.profile = state.matchData.suggested_profile || 'qwen-dflash';
    if (!state.port) state.port = state.matchData.suggested_port || 8093;
    if (mode !== 'replace' && !state.draftPath && state.matchData.local_accelerators?.length) {
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
      id: state.serverIdNew || undefined,
      copy_to_console: (state.copyMode || 'copy') !== 'none',
      copy_mode: state.copyMode || 'copy',
      overwrite: false,
    };
    let result = await api('/api/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (result?.exists) {
      const existing = result.existing_path || result.filename || 'the Console library';
      const overwrite = window.confirm(
        `${result.error || 'These model files are already in the DFlash Console library.'}\n\nExisting: ${existing}\n\nReplace the existing copy?`,
      );
      if (!overwrite) return null;
      result = await api('/api/servers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, overwrite: true }),
      });
    }
    if (result?.exists || !result?.server) return null;
    closeModal();
    toast(`Created ${result.server?.label || 'DFlash stack'}`);
    await window.DFlashModelsLive?.refresh?.();
    await window.DFlashServerLive?.refresh?.();
    const serverId = result.server?.id;
    if (serverId) {
      window.DFlashModelsLive?.setTypeFilter?.('dflash');
      await window.DFlashModelsLive?.selectModel?.(serverId);
    }
    if (typeof state.onAttached === 'function') {
      await state.onAttached(result);
    }
    return result;
  }

  async function goNext() {
    if (mode === 'replace') {
      if (!state.draftPath || samePath(state.draftPath, state.currentDraftPath)) return;
      await applyReplaceDraft(state.draftPath, state.draftLabel);
      return;
    }
    if (step === 1) {
      if (!state.targetPath) return;
      await loadMatchData();
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
    if (mode === 'replace' || step <= 1) return;
    step -= 1;
    render();
  }

  async function open(options = {}) {
    mode = 'create';
    step = 1;
    hideCatalogReturn();
    state = {
      targetPath: options.targetPath || '',
      targetLabel: options.targetLabel || '',
      draftPath: options.draftPath || '',
      draftLabel: options.draftLabel || '',
      currentDraftPath: '',
      currentDraftLabel: '',
      serverId: '',
      stackLabel: '',
      matchData: null,
      targetNotice: '',
      label: options.label || '',
      serverIdNew: '',
      modelId: '',
      profile: '',
      port: 0,
      copyMode: 'copy',
      dflashGeneration: inferGenerationFromPath(options.draftPath || ''),
      generationTouched: Boolean(options.draftPath),
      knownAcceleratorPaths: new Set(),
      awaitingCatalog: false,
      onAttached: options.onAttached || null,
    };

    try {
      await loadCapableTargets();

      if (options.targetPath) {
        const pairedSetup = Boolean(options.pairedSetup && options.targetPath && options.draftPath);
        const inList = capableTargets.some((row) => samePath(row.path, options.targetPath));
        if (!pairedSetup && !inList && !isAcceleratorPath(options.draftPath)) {
          if (options.allowHfAccelerator) {
            if (!state.targetLabel) {
              state.targetLabel = options.targetPath.split(/[/\\]/).pop() || '';
            }
          } else {
            state.targetNotice = 'No compatible DFlash accelerator was found on this PC. Download one first, then reopen the wizard.';
            state.targetPath = '';
            state.targetLabel = '';
          }
        } else if (!state.targetLabel) {
          const model = capableTargets.find((row) => samePath(row.path, options.targetPath));
          state.targetLabel = model?.label || model?.filename || options.targetLabel || options.targetPath.split(/[/\\]/).pop() || '';
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

  function inferGenerationFromPath(path) {
    return /dflash[-_.]?2|dflash2/i.test(String(path || '')) ? 'dflash2' : 'dflash1';
  }

  async function openReplaceDraft(options = {}) {
    mode = 'replace';
    step = 1;
    hideCatalogReturn();
    stopReplaceDownloadWatch();
    const currentDraftPath = options.currentDraftPath || options.draftPath || '';
    state = {
      targetPath: options.targetPath || '',
      targetLabel: options.targetLabel || '',
      draftPath: '',
      draftLabel: '',
      currentDraftPath,
      currentDraftLabel: options.currentDraftLabel || options.draftLabel || '',
      serverId: options.serverId || '',
      stackLabel: options.label || options.targetLabel || '',
      matchData: null,
      targetNotice: '',
      label: options.label || '',
      serverIdNew: '',
      modelId: '',
      profile: '',
      port: 0,
      dflashGeneration: inferGenerationFromPath(currentDraftPath),
      generationTouched: false,
      knownAcceleratorPaths: new Set(),
      awaitingCatalog: false,
      onAttached: options.onAttached || null,
    };

    if (state.currentDraftPath && !state.currentDraftLabel) {
      state.currentDraftLabel = state.currentDraftPath.split(/[/\\]/).pop() || '';
    }

    try {
      window.DFlashStatusFeed?.setTransient?.('Checking accelerators…', {
        secondary: state.stackLabel || state.targetLabel || '',
        ttlMs: 15000,
      });
      await loadMatchData();
      if (state.matchData?.best_local?.path) {
        state.draftPath = state.matchData.best_local.path;
        state.draftLabel = state.matchData.best_local.filename || '';
      }
      openModal();
      render();
      requestAnimationFrame(syncDialogSize);
    } catch (err) {
      toast(err.message || 'Could not check accelerators', false);
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
  window.DFlashStackWizard = { open, openReplaceDraft, close: closeModal, resumeFromCatalog };
})();
