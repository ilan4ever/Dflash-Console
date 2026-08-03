/** Models tab — local catalog + inspector binding */
(function () {
  const { api, toast } = window.ConsoleApi;

  let models = [];
  let meta = {};
  let selectedKey = localStorage.getItem('dflashConsole.selectedModelKey') || '';
  let loadedServerIds = new Set();
  let bootingServers = {};
  let contextModel = null;
  let stackPreflight = { key: '', status: 'idle', result: null };

  const PINNED_KEY = 'dflashConsole.pinnedModels';

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function modelKey(model) {
    return model.server_id || model.path || model.id;
  }

  function loadPinnedSet() {
    try {
      const list = JSON.parse(localStorage.getItem(PINNED_KEY) || '[]');
      return new Set(Array.isArray(list) ? list : []);
    } catch {
      return new Set();
    }
  }

  function savePinnedSet(set) {
    localStorage.setItem(PINNED_KEY, JSON.stringify([...set]));
  }

  function loadBrowsePrefs() {
    try {
      return JSON.parse(localStorage.getItem('dflashConsole.modelPrefs') || '{}');
    } catch {
      return {};
    }
  }

  function dflashLogoLabel(label = 'DFlash') {
    const safeLabel = escapeHtml(label);
    return `<span class="lm-tag gold dflash-logo-label" role="img" aria-label="${safeLabel}" title="${safeLabel}"></span>`;
  }

  function capabilityTags(caps, { loadable = false, port = 0 } = {}) {
    const list = Array.isArray(caps) ? caps : [];
    const tags = [];
    if (loadable) {
      tags.push('<span class="lm-tag green">loadable</span>');
      tags.push(`<span class="lm-tag blue">port ${port || '—'}</span>`);
    }
    if (list.includes('tools')) tags.push('<span class="lm-tag green">tools</span>');
    if (list.includes('vision')) tags.push('<span class="lm-tag purple">vision</span>');
    if (list.includes('ar')) tags.push('<span class="lm-tag blue">AR</span>');
    list.forEach((cap) => {
      if (cap === 'instruct' || cap === 'tools' || cap === 'vision' || cap === 'ar' || cap === 'dflash') return;
      tags.push(`<span class="lm-tag blue">${escapeHtml(cap)}</span>`);
    });
    return tags.join('');
  }

  function serverHasModelOnGpu(server) {
    if (!server) return false;
    if (server.status === 'loaded') return true;
    const loaded = server.loaded_models;
    return Array.isArray(loaded) && loaded.length > 0;
  }

  function isStackLoadedOnGpu(model) {
    return !!(model.server_id && loadedServerIds.has(model.server_id));
  }

  function isModelReadyToLoad(model) {
    return !!(model?.loadable && !isStackLoadedOnGpu(model) && !isStackBooting(model));
  }

  function isStackReadyToLoad(model) {
    return isModelReadyToLoad(model) && isDflashStack(model);
  }

  function isStackBooting(model) {
    return !!(model.server_id && bootingServers[model.server_id]);
  }

  function loadedRibbon(model) {
    if (!isStackLoadedOnGpu(model)) return '';
    return '<div class="lm-model-loaded-ribbon" title="Model weights are on GPU">loaded</div>';
  }

  function modelRowClassName(model, { selected = false, pinned = false } = {}) {
    const parts = ['lm-model-row'];
    if (selected) parts.push('selected');
    if (pinned) parts.push('pinned');
    if (isStackBooting(model)) parts.push('loading-on-server');
    else if (isStackLoadedOnGpu(model)) parts.push('loaded-on-gpu');
    else if (isModelReadyToLoad(model)) parts.push('ready-to-load');
    return parts.join(' ');
  }

  function modelHasVision(model) {
    const caps = Array.isArray(model?.capabilities) ? model.capabilities : [];
    return caps.includes('vision');
  }

  function canAddVision(model) {
    return !!model?.path && !modelHasVision(model);
  }

  function capTags(model) {
    const status = isDflashStack(model) ? stackStatusTag(model) : '';
    const dup = duplicateTag(model);
    const weak = weakMatchTag(model);
    const caps = capabilityTags(model.capabilities, { loadable: model.loadable, port: model.port });
    if (isDflashStack(model)) {
      return status + caps + dup + weak;
    }
    return status + dup + weak + caps;
  }

  function stackActionButton(model) {
    if (isStackBooting(model)) {
      return '<span class="lm-tag dim">loading…</span>';
    }
    if (isStackLoadedOnGpu(model)) {
      return '<button class="lm-btn ghost tiny" type="button" data-action="unload-model" title="Remove model from GPU">Unload</button>';
    }
    if (model.loadable) {
      return '<button class="lm-btn ghost tiny" type="button" data-action="load-model" title="Load model onto GPU">Load</button>';
    }
    if (model.stack_status === 'disabled' && model.server_id) {
      return '<button class="lm-btn ghost tiny" type="button" data-action="enable-stack">Enable</button>';
    }
    if ((model.stack_status === 'unregistered' || model.dflash_stack) && model.path && model.draft_path) {
      return `<button class="lm-btn ghost tiny" type="button" data-action="load-model" title="Load target only as a standard LLM">Load LLM</button>`
        + `<button class="lm-btn ghost tiny" type="button" data-action="setup-stack">DFlash stack</button>`;
    }
    if (model.path && (model.plain_gguf || model.loadable || !model.server_id)) {
      return '<button class="lm-btn ghost tiny" type="button" data-action="load-model" title="Load onto the active engine">Load</button>';
    }
    return '<span class="lm-tag dim" title="No local file">browse</span>';
  }

  const STACK_SORT = { ready: 0, disabled: 1, unregistered: 2 };

  function isDflashModel(model) {
    const caps = Array.isArray(model.capabilities) ? model.capabilities : [];
    return caps.includes('dflash') || (model.loadable && !!model.draft_path);
  }

  function isDflashStack(model) {
    return !!model.dflash_stack || isDflashModel(model);
  }

  function stackStatusTag(model) {
    if (isStackBooting(model)) {
      return '<span class="lm-tag blue">loading</span>';
    }
    if (isStackLoadedOnGpu(model)) {
      return '';
    }
    if (model.stack_status === 'ready' || (model.loadable && isDflashStack(model))) {
      return '<span class="lm-tag green">ready</span>';
    }
    if (model.stack_status === 'disabled') {
      return '<span class="lm-tag yellow">disabled profile</span>';
    }
    if (model.stack_status === 'unregistered') {
      return '<span class="lm-tag blue">needs setup</span>';
    }
    return '';
  }

  function duplicateTag(model) {
    if (!model.duplicate_group) return '';
    const count = model.duplicate_count || 2;
    return `<span class="lm-tag yellow" title="This exact filename exists in ${count} folders on disk. Pick one copy for your stack; delete or ignore the rest.">duplicate ×${count}</span>`;
  }

  function weakMatchTag(model) {
    if (model.stack_status !== 'unregistered') return '';
    const score = Number(model.match_score || 0);
    if (score > 0 && score < 7) {
      return `<span class="lm-tag yellow" title="Accelerator pairing score ${score.toFixed(1)} — verify target and draft before setup.">weak match</span>`;
    }
    return '';
  }

  function shortPath(path) {
    const text = String(path || '').replace(/\\/g, '/');
    const parts = text.split('/');
    if (parts.length <= 3) return text;
    return `…/${parts.slice(-3).join('/')}`;
  }

  function modelPathHint(model) {
    if (!model.path) return '';
    const showPath = model.duplicate_group
      || (model.dflash_stack && model.filename && model.label !== model.filename)
      || (model.stack_status === 'unregistered');
    if (!showPath) return '';
    return `<div class="lm-model-path-hint" title="${escapeHtml(model.path)}">${escapeHtml(shortPath(model.path))}</div>`;
  }

  function isDflashAccelerator(model) {
    if (model.loadable && isDflashModel(model)) return false;
    const name = `${model.filename || ''} ${model.label || ''}`.toLowerCase();
    if (!name || name.startsWith('mmproj')) return false;
    return /dflash|dspark/.test(name);
  }

  function draftHint(model) {
    if (!model.draft_filename && !model.draft_path) return '';
    const name = model.draft_filename || model.draft_path.split(/[/\\]/).pop();
    const size = model.draft_size_gb != null ? ` · ${model.draft_size_gb} GB` : '';
    const quant = model.draft_quant && model.draft_quant !== '—' ? ` ${model.draft_quant}` : '';
    return `<div class="lm-model-draft-hint">draft ${escapeHtml(name)}${escapeHtml(quant)}${escapeHtml(size)}</div>`;
  }

  function mergeModelsWithState(catalogModels, serversData, browsePrefs) {
    const serverMap = {};
    loadedServerIds = new Set();
    bootingServers = {};
    for (const server of serversData.servers || []) {
      serverMap[server.id] = server;
      if (serverHasModelOnGpu(server)) {
        loadedServerIds.add(server.id);
      }
      if (server.status === 'booting') {
        bootingServers[server.id] = {
          progress: server.load_progress,
          label: server.label || server.id,
        };
      }
    }
    return catalogModels.map((model) => {
      const key = modelKey(model);
      let merged = { ...model };
      if (model.server_id && serverMap[model.server_id]) {
        const server = serverMap[model.server_id];
        merged = {
          ...merged,
          context_size: server.context_size ?? merged.context_size,
          load_settings: { ...(merged.load_settings || {}), ...(server.load_settings || {}) },
          inference_settings: { ...(merged.inference_settings || {}), ...(server.inference_settings || {}) },
          runtime_status: server.status,
          runtime_loaded: serverHasModelOnGpu(server),
          runtime_booting: server.status === 'booting',
          runtime_progress: server.load_progress,
        };
      } else {
        const prefs = browsePrefs[key];
        if (prefs) {
          merged = {
            ...merged,
            context_size: prefs.context_size ?? merged.context_size,
            load_settings: { ...(merged.load_settings || {}), ...(prefs.load_settings || {}) },
            inference_settings: { ...(merged.inference_settings || {}), ...(prefs.inference_settings || {}) },
          };
        }
      }
      return merged;
    });
  }

  function modelIdentifier(model) {
    return model.server_id || model.path || model.id || '';
  }

  function stackTargetIssue(model) {
    if (!model?.path) {
      return {
        reason_code: 'no-path',
        reason: 'No local GGUF file is attached to this entry.',
      };
    }
    if (isDflashStack(model)) {
      return {
        reason_code: 'already-stack',
        reason: 'This model is already registered as a DFlash stack.',
      };
    }
    if (isDflashAccelerator(model)) {
      return {
        reason_code: 'accelerator',
        reason: 'This is a DFlash accelerator. Choose the full target GGUF instead.',
      };
    }
    if (!/\.gguf$/i.test(String(model.path))) {
      return {
        reason_code: 'not-gguf',
        reason: 'DFlash stacks require a full GGUF target model.',
      };
    }
    if (!modelIdentifier(model)) {
      return {
        reason_code: 'no-identifier',
        reason: 'This model has no usable identifier, so a stack cannot be registered.',
      };
    }
    return null;
  }

  function stackMenuState(model) {
    const issue = stackTargetIssue(model);
    if (issue) return { status: 'unavailable', result: issue };
    if (stackPreflight.key !== modelKey(model)) {
      return { status: 'checking', result: null };
    }
    return stackPreflight;
  }

  function stackMenuActionHtml(model) {
    const state = stackMenuState(model);
    const result = state.result || {};
    if (state.status === 'checking') {
      return `
        <button type="button" data-cmd="create-stack" disabled title="Checking for a compatible DFlash accelerator">
          Checking DFlash compatibility…
        </button>
        <div class="df-stack-preflight df-model-stack-preflight is-checking">Looking for a matching accelerator on this PC.</div>`;
    }
    if (state.status === 'ready' && result.eligible) {
      const accelerator = result.best_accelerator?.filename || 'compatible accelerator';
      return `
        <button type="button" data-cmd="create-stack" title="Open the DFlash stack wizard">
          Create DFlash stack…
        </button>
        <div class="df-stack-preflight df-model-stack-preflight is-ready">Ready to pair with ${escapeHtml(accelerator)}.</div>`;
    }
    return `
      <button type="button" data-cmd="create-stack" disabled title="${escapeHtml(result.reason || 'DFlash stack is not available')}">
        DFlash stack unavailable
      </button>
      <div class="df-stack-preflight df-model-stack-preflight is-unavailable">${escapeHtml(result.reason || 'This model cannot be used for a DFlash stack.')}</div>`;
  }

  function updateStackMenuAction(model) {
    const menu = document.getElementById('modelsContextMenu');
    const slot = menu?.querySelector('#modelsStackAction');
    if (!slot || contextModel !== model) return;
    slot.innerHTML = stackMenuActionHtml(model);
    const button = slot.querySelector('button[data-cmd="create-stack"]');
    button?.addEventListener('click', (event) => {
      event.stopPropagation();
      void runContextCommand('create-stack', model);
      hideContextMenu();
    });
  }

  async function checkStackPreflight(model) {
    const key = modelKey(model);
    try {
      const result = await api(`/api/stacks/preflight?target_path=${encodeURIComponent(model.path)}`, {
        timeoutMs: 10000,
      });
      if (contextModel !== model || stackPreflight.key !== key) return;
      stackPreflight = {
        key,
        status: result.eligible ? 'ready' : 'unavailable',
        result,
      };
      updateStackMenuAction(model);
    } catch (err) {
      if (contextModel !== model || stackPreflight.key !== key) return;
      stackPreflight = {
        key,
        status: 'unavailable',
        result: {
          reason_code: 'check-failed',
          reason: err.message || 'Could not check this model for DFlash compatibility.',
        },
      };
      updateStackMenuAction(model);
    }
  }

  function huggingFaceUrl(model) {
    const normalized = String(model.path || '').replace(/\\/g, '/');
    const parts = normalized.split('/');
    const modelsIdx = parts.findIndex((part) => part === 'models');
    if (modelsIdx >= 0 && parts.length > modelsIdx + 2) {
      return `https://huggingface.co/${parts[modelsIdx + 1]}/${parts[modelsIdx + 2]}`;
    }
    if (model.publisher && model.id) {
      return `https://huggingface.co/${model.publisher}/${model.id}`;
    }
    return '';
  }

  function getActiveDownloadJobs() {
    return (window.DFlashDownloadQueue?.getActiveJobs?.() || [])
      .filter((job) => job.status === 'downloading');
  }

  function downloadProgressWidth(job) {
    return window.DFlashDownloadQueue?.progressWidth?.(job);
  }

  function downloadProgressLabel(job) {
    return window.DFlashDownloadQueue?.progressLabel?.(job) || 'Starting…';
  }

  function formatSizeGb(sizeGb) {
    if (sizeGb == null || Number.isNaN(Number(sizeGb))) return '—';
    const gb = Number(sizeGb);
    if (gb <= 0) return '—';
    if (gb < 0.01) return `${Math.max(1, Math.round(gb * 1024))} MB`;
    return `${gb} GB`;
  }

  function modelTitleLine(model) {
    const parts = [model.label || model.id || '—'];
    if (model.quant && model.quant !== '—') parts.push(model.quant);
    const sizeLabel = formatSizeGb(model.size_gb);
    if (sizeLabel !== '—') parts.push(sizeLabel);
    return parts.join(' · ');
  }

  function installedBadge(model) {
    return model?.path ? '<span class="lm-badge installed">Installed</span>' : '';
  }

  function installedDflashLogo(model) {
    if (!model?.path || model?.path_missing || !isDflashModel(model)) return '';
    return dflashLogoLabel('DFlash');
  }

  function downloadJobTitle(job) {
    return window.DFlashDownloadQueue?.getJobLabel?.(job)
      || job.filename
      || job.repo_id
      || 'Model';
  }

  function renderDownloadingRow(job) {
    const width = downloadProgressWidth(job);
    const pctLabel = downloadProgressLabel(job);
    const indeterminate = width == null;
    const fillStyle = width != null ? ` style="width:${width}%"` : '';
    const fillClass = indeterminate ? ' is-indeterminate' : '';
    const bytes = job.bytes_total
      ? `${window.DFlashDownloadQueue?.formatBytes?.(job.bytes_read) || '0 B'} / ${window.DFlashDownloadQueue?.formatBytes?.(job.bytes_total) || '—'}`
      : (job.bytes_read ? window.DFlashDownloadQueue?.formatBytes?.(job.bytes_read) : '—');
    const repo = job.repo_id || 'Hugging Face';
    const filename = job.filename || '—';
    const title = downloadJobTitle(job);
    const titleLine = bytes && bytes !== '—' ? `${title} · ${bytes}` : title;
    return `
      <tr class="lm-model-row downloading-model" data-download-job-id="${escapeHtml(job.id)}">
        <td class="lm-col-model">
          <div class="lm-model-title-line">
            <span class="lm-tag green">downloading</span>
            <span class="lm-model-title-text">${escapeHtml(titleLine)}</span>
            <span class="lm-model-download-pct">${escapeHtml(pctLabel)}</span>
          </div>
          <div class="lm-model-download-bar" aria-hidden="true">
            <div class="lm-model-download-fill${fillClass}"${fillStyle}></div>
          </div>
          <div class="lm-model-meta-line lm-model-download-meta">${escapeHtml(repo)} · ${escapeHtml(filename)}</div>
        </td>
        <td class="lm-col-meta">—</td>
        <td class="lm-col-meta">—</td>
        <td class="lm-col-meta">${escapeHtml(repo.split('/')[0] || 'HF')}</td>
        <td class="lm-col-meta">${escapeHtml(bytes)}</td>
        <td class="lm-col-meta">Now</td>
        <td class="lm-col-action"><span class="lm-tag dim">in progress</span></td>
      </tr>`;
  }

  function filterDownloadJobs(jobs, needle) {
    if (!needle) return jobs;
    return jobs.filter((job) => {
      const hay = [
        downloadJobTitle(job),
        job.repo_id,
        job.filename,
        job.path,
      ].join(' ').toLowerCase();
      return hay.includes(needle);
    });
  }

  const TYPE_FILTER_KEY = 'dflashConsole.modelsTypeFilter.v2';
  let typeFilter = localStorage.getItem(TYPE_FILTER_KEY) || 'all';
  const MODEL_TYPE_FILTER_KEY = 'dflashConsole.modelsModelTypeFilter';
  const MODEL_TYPE_FILTERS = new Set([
    'all',
    'llm',
    'dflash',
    'ocr',
    'translation',
    'speech-to-text',
    'text-to-speech',
    'embedding',
    'vision',
    'other',
  ]);
  let modelTypeFilter = localStorage.getItem(MODEL_TYPE_FILTER_KEY) || 'all';
  if (!MODEL_TYPE_FILTERS.has(modelTypeFilter)) modelTypeFilter = 'all';

  let pollTimer = null;
  let pollPaused = false;
  let lastRenderSignature = '';

  function modelType(model) {
    const caps = Array.isArray(model?.capabilities) ? model.capabilities : [];
    const haystack = [
      model?.label,
      model?.filename,
      model?.path,
      model?.publisher,
      model?.pipeline_tag,
    ].join(' ').toLowerCase();
    if (caps.includes('dflash') || model?.dflash_stack || /dflash|dspark/.test(haystack)) return 'dflash';
    if (/ocr|chandra|ovis|paddleocr|olmocr/.test(haystack)) return 'ocr';
    if (/translat|nllb|madlad|seamless|tower/.test(haystack)) return 'translation';
    if (/whisper|speech|asr|parakeet|wav2vec|faster-whisper/.test(haystack)) return 'speech-to-text';
    if (/text.?to.?speech|tts|piper|kokoro|bark/.test(haystack)) return 'text-to-speech';
    if (/embed|embedding|nomic|bge[-_]|e5[-_]|gte[-_]/.test(haystack)) return 'embedding';
    if (caps.includes('vision') || /vision|multimodal|[-_]vl[-_]|image/.test(haystack)) return 'vision';
    if (caps.includes('llm') || caps.includes('instruct')) return 'llm';
    return 'other';
  }

  function matchesModelType(model) {
    return modelTypeFilter === 'all' || modelType(model) === modelTypeFilter;
  }

  function modelsRenderSignature(filterText) {
    const needle = String(filterText || '').trim().toLowerCase();
    const rows = models.filter((model) => {
      if (typeFilter === 'dflash' && !isDflashStack(model)) return false;
      if (typeFilter === 'accelerators' && !isDflashAccelerator(model)) return false;
      if (typeFilter === 'loaded' && !isStackLoadedOnGpu(model)) return false;
      if (typeFilter === 'downloading') return false;
      if (!matchesModelType(model)) return false;
      if (!needle) return true;
      const hay = [
        model.label, model.id, model.path, model.publisher, model.arch, model.quant,
        model.draft_label, model.draft_filename, model.draft_path, model.stack_status,
      ].join(' ').toLowerCase();
      return hay.includes(needle);
    }).map((model) => [
      modelKey(model),
      model.runtime_status,
      model.runtime_loaded,
      model.stack_status,
      model.duplicate_group,
      model.match_score,
    ].join(':')).join('|');
    return `${typeFilter}:${modelTypeFilter}:${selectedKey}:${needle}:${rows}`;
  }

  function renderTable(filterText, { force = false } = {}) {
    const body = document.getElementById('modelsTableBody');
    if (!body) return;
    const signature = modelsRenderSignature(filterText);
    if (!force && signature === lastRenderSignature) return;
    lastRenderSignature = signature;
    const needle = String(filterText || '').trim().toLowerCase();
    const pinned = loadPinnedSet();
    const activeDownloads = filterDownloadJobs(getActiveDownloadJobs(), needle);
    const catalogRows = models.filter((model) => {
      if (typeFilter === 'dflash' && !isDflashStack(model)) return false;
      if (typeFilter === 'accelerators' && !isDflashAccelerator(model)) return false;
      if (typeFilter === 'loaded' && !isStackLoadedOnGpu(model)) return false;
      if (typeFilter === 'downloading') return false;
      if (!matchesModelType(model)) return false;
      if (!needle) return true;
      const hay = [
        model.label, model.id, model.path, model.publisher, model.arch, model.quant,
        model.draft_label, model.draft_filename, model.draft_path, model.stack_status,
      ].join(' ').toLowerCase();
      return hay.includes(needle);
    }).sort((a, b) => {
      const aPin = pinned.has(modelKey(a)) ? 0 : 1;
      const bPin = pinned.has(modelKey(b)) ? 0 : 1;
      if (aPin !== bPin) return aPin - bPin;
      if (typeFilter === 'dflash') {
        const aStack = STACK_SORT[a.stack_status || (a.loadable ? 'ready' : 'unregistered')] ?? 3;
        const bStack = STACK_SORT[b.stack_status || (b.loadable ? 'ready' : 'unregistered')] ?? 3;
        if (aStack !== bStack) return aStack - bStack;
      }
      const aScore = (a.loadable ? 0 : 1) + (isDflashStack(a) ? 0 : 0.5);
      const bScore = (b.loadable ? 0 : 1) + (isDflashStack(b) ? 0 : 0.5);
      if (aScore !== bScore) return aScore - bScore;
      return String(a.label || '').localeCompare(String(b.label || ''));
    });

    if (typeFilter === 'downloading') {
      body.innerHTML = activeDownloads.length
        ? activeDownloads.map((job) => renderDownloadingRow(job)).join('')
        : '<tr><td colspan="7" class="lm-models-empty">No models are downloading right now. Start a download from Model catalog or use Add vision support on a model row.</td></tr>';
      return;
    }

    if (!catalogRows.length && !(typeFilter === 'loaded' ? [] : activeDownloads).length) {
      const emptyLabel = typeFilter === 'dflash'
        ? 'No DFlash stacks found. Use Create DFlash stack or check Settings → model folders.'
        : typeFilter === 'accelerators'
          ? 'No accelerator files found. They are small DFlash/DSpark draft checkpoints on disk.'
          : typeFilter === 'loaded'
            ? 'No models are loaded on the GPU right now. Use Load on a model row or the Engines tab.'
            : 'No models match this filter.';
      body.innerHTML = `<tr><td colspan="7" class="lm-models-empty">${escapeHtml(emptyLabel)}</td></tr>`;
      return;
    }

    body.innerHTML = [
      ...(typeFilter === 'loaded' ? [] : activeDownloads.map((job) => renderDownloadingRow(job))),
      ...catalogRows.map((model) => {
      const key = modelKey(model);
      const selected = key === selectedKey;
      const pinnedClass = pinned.has(key) ? ' pinned' : '';
      const pinMark = pinned.has(key) ? '<span class="lm-model-pin" title="Pinned">📌</span>' : '';
      const loadBtn = stackActionButton(model);
      const size = formatSizeGb(model.size_gb);
      return `
        <tr class="${modelRowClassName(model, { selected, pinned: pinned.has(key) })}" data-model-key="${escapeHtml(key)}" data-server-id="${escapeHtml(model.server_id || '')}">
          <td class="lm-col-model">
            ${loadedRibbon(model)}
            <div class="lm-model-title-line"><span class="lm-model-title-text">${escapeHtml(modelTitleLine(model))}</span>${installedBadge(model)}${installedDflashLogo(model)}${pinMark}</div>
            <div class="lm-model-tags-line">${capTags(model)}</div>
            ${modelPathHint(model)}
            ${draftHint(model)}
          </td>
          <td class="lm-col-meta">${escapeHtml(model.arch || '—')}</td>
          <td class="lm-col-meta">${escapeHtml(model.params || '—')}</td>
          <td class="lm-col-meta">${escapeHtml(model.publisher || '—')}</td>
          <td class="lm-col-meta">${escapeHtml(size)}</td>
          <td class="lm-col-meta">${escapeHtml(model.modified || '—')}</td>
          <td class="lm-col-action">${loadBtn}</td>
        </tr>`;
    }),
    ].join('');

    body.querySelectorAll('.lm-model-row:not(.downloading-model)').forEach((row) => {
      row.addEventListener('click', (event) => {
        if (event.target.closest('[data-action="load-model"]')) return;
        if (event.target.closest('[data-action="browse-model"]')) return;
        if (event.target.closest('[data-action="enable-stack"]')) return;
        if (event.target.closest('[data-action="setup-stack"]')) return;
        if (event.target.closest('[data-action="unload-model"]')) return;
        void selectModel(row.dataset.modelKey);
      });
      row.addEventListener('dblclick', () => {
        const model = models.find((entry) => modelKey(entry) === row.dataset.modelKey);
        if (model?.loadable) void loadModel(model);
      });
      row.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        const model = models.find((entry) => modelKey(entry) === row.dataset.modelKey);
        if (model) openContextMenu(event, model);
      });
    });
    body.querySelectorAll('[data-action="load-model"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const row = btn.closest('.lm-model-row');
        const model = models.find((entry) => modelKey(entry) === row?.dataset.modelKey);
        if (model) void loadModel(model);
      });
    });
    body.querySelectorAll('[data-action="browse-model"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const row = btn.closest('.lm-model-row');
        const model = models.find((entry) => modelKey(entry) === row?.dataset.modelKey);
        if (model) void browseModel(model);
      });
    });
    body.querySelectorAll('[data-action="enable-stack"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const row = btn.closest('.lm-model-row');
        const model = models.find((entry) => modelKey(entry) === row?.dataset.modelKey);
        if (model) void enableStack(model);
      });
    });
    body.querySelectorAll('[data-action="setup-stack"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const row = btn.closest('.lm-model-row');
        const model = models.find((entry) => modelKey(entry) === row?.dataset.modelKey);
        if (model) setupStack(model);
      });
    });
    body.querySelectorAll('[data-action="unload-model"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const row = btn.closest('.lm-model-row');
        const model = models.find((entry) => modelKey(entry) === row?.dataset.modelKey);
        if (model) void unloadModel(model);
      });
    });
  }

  async function unloadModel(model) {
    if (!model?.server_id) {
      toast('No engine profile for this stack', false);
      return;
    }
    try {
      await api(`/api/servers/${encodeURIComponent(model.server_id)}/unload`, { method: 'POST' });
      toast(`${model.label || model.server_id} unloaded`);
      await refresh({ rebindInspector: true });
      if (window.DFlashServerLive?.refresh) {
        await window.DFlashServerLive.refresh(true);
      }
    } catch (err) {
      toast(err.message, false);
    }
  }

  async function enableStack(model) {
    if (!model?.server_id) {
      toast('No engine profile to enable', false);
      return;
    }
    try {
      await api(`/api/servers/${encodeURIComponent(model.server_id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }),
      });
      toast(`${model.label || model.server_id} enabled`);
      await refresh({ rebindInspector: true });
    } catch (err) {
      toast(err.message, false);
    }
  }

  function setupStack(model) {
    if (!model?.path || !model?.draft_path) {
      toast('Missing target or accelerator path', false);
      return;
    }
    window.DFlashStackWizard?.open?.({
      targetPath: model.path,
      targetLabel: model.label || model.filename,
      draftPath: model.draft_path,
      draftLabel: model.draft_filename || model.draft_label,
    });
  }

  async function browseModel(model) {
    if (!model?.path) {
      toast('No file path for this model', false);
      return;
    }
    try {
      await api(`/api/fs/reveal?path=${encodeURIComponent(model.path)}`, { method: 'POST' });
    } catch (err) {
      toast(err.message, false);
    }
  }

  function hideContextMenu() {
    const menu = document.getElementById('modelsContextMenu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML = '';
    contextModel = null;
    stackPreflight = { key: '', status: 'idle', result: null };
  }

  function openContextMenu(event, model) {
    const menu = document.getElementById('modelsContextMenu');
    if (!menu) return;
    contextModel = model;
    const key = modelKey(model);
    const pinned = loadPinnedSet();
    const isPinned = pinned.has(key);
    const hfUrl = huggingFaceUrl(model);
    const canDelete = !!model.path && !model.loadable;

    const targetIssue = stackTargetIssue(model);
    const targetKey = modelKey(model);
    stackPreflight = targetIssue
      ? { key: targetKey, status: 'unavailable', result: targetIssue }
      : { key: targetKey, status: 'checking', result: null };

    menu.innerHTML = `
      <button type="button" data-cmd="pin">${isPinned ? 'Unpin' : 'Pin'}</button>
      <button type="button" data-cmd="copy-id">Copy identifier</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <button type="button" data-cmd="huggingface"${hfUrl ? '' : ' disabled'}>Open Hugging Face</button>
      <button type="button" data-cmd="add-vision"${canAddVision(model) ? '' : ' disabled'} title="Download vision projector from Hugging Face and wire it to this model">Add vision support…</button>
      <hr>
      <div id="modelsStackAction">${stackMenuActionHtml(model)}</div>
      <button type="button" data-cmd="load"${model.loadable ? '' : ' disabled'}>Load to Server</button>
      <button type="button" data-cmd="delete"${canDelete ? '' : ' disabled'}>Delete</button>`;

    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;

    menu.querySelectorAll('button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        void runContextCommand(btn.dataset.cmd, model);
        hideContextMenu();
      });
    });
    if (!targetIssue) void checkStackPreflight(model);
  }

  async function runContextCommand(cmd, model) {
    const key = modelKey(model);
    if (cmd === 'pin') {
      const pinned = loadPinnedSet();
      if (pinned.has(key)) pinned.delete(key);
      else pinned.add(key);
      savePinnedSet(pinned);
      renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
      toast(pinned.has(key) ? 'Model pinned' : 'Model unpinned');
      return;
    }
    if (cmd === 'copy-id') {
      const id = modelIdentifier(model);
      if (!id) return;
      await navigator.clipboard.writeText(id);
      toast('Identifier copied');
      return;
    }
    if (cmd === 'metadata') {
      const modal = document.getElementById('modelMetadataModal');
      const pre = document.getElementById('modelMetadataBody');
      if (pre) pre.textContent = JSON.stringify(model, null, 2);
      modal?.classList.add('open');
      modal?.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      return;
    }
    if (cmd === 'add-vision') {
      if (canAddVision(model)) await setupVision(model);
      return;
    }
    if (cmd === 'huggingface') {
      const url = huggingFaceUrl(model);
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
      else toast('No Hugging Face URL for this model', false);
      return;
    }
    if (cmd === 'create-stack') {
      const check = stackPreflight.key === key ? stackPreflight.result : null;
      if (stackPreflight.status !== 'ready' || !check?.eligible) {
        toast(check?.reason || 'This model is not ready for a DFlash stack', false);
        return;
      }
      const accel = isDflashAccelerator(model);
      window.DFlashStackWizard?.open?.({
        targetPath: accel ? '' : model.path,
        targetLabel: accel ? '' : (model.label || model.filename),
        draftPath: accel ? model.path : '',
        draftLabel: accel ? (model.filename || model.label) : '',
      });
      return;
    }
    if (cmd === 'load') {
      if (model.loadable) await loadModel(model);
      return;
    }
    if (cmd === 'delete') {
      if (!model.path || model.loadable) {
        toast('Only browse-only local GGUF files can be deleted here', false);
        return;
      }
      const name = model.filename || model.label || 'this file';
      if (!window.confirm(`Delete ${name}? This cannot be undone.`)) return;
      try {
        await api(`/api/models/file?path=${encodeURIComponent(model.path)}`, { method: 'DELETE' });
        toast('Model file deleted');
        if (selectedKey === key) selectedKey = '';
        await refresh({ rebindInspector: true });
      } catch (err) {
        toast(err.message, false);
      }
    }
  }

  function renderFooter(data) {
    meta = data || meta;
    const stats = document.getElementById('modelsFooterStats');
    const path = document.getElementById('modelsFooterPath');
    const hint = document.getElementById('modelsFooterHint');
    if (stats) {
      const activeCount = getActiveDownloadJobs().length;
      if (typeFilter === 'downloading') {
        stats.textContent = `${activeCount} downloading now`;
      } else {
        const shown = typeFilter === 'dflash'
          ? models.filter(isDflashStack).length
          : typeFilter === 'accelerators'
            ? models.filter(isDflashAccelerator).length
            : typeFilter === 'loaded'
              ? models.filter(isStackLoadedOnGpu).length
              : models.length;
        const readyStacks = models.filter((model) => isDflashStack(model) && model.loadable).length;
        const filterNote = typeFilter === 'dflash'
          ? ` · showing ${shown} DFlash stacks (${readyStacks} ready to load)`
          : typeFilter === 'accelerators'
            ? ` · showing ${shown} accelerators`
            : typeFilter === 'loaded'
              ? ` · showing ${shown} loaded on GPU`
              : '';
        const downloadNote = activeCount ? ` · ${activeCount} downloading` : '';
        stats.textContent = `${meta.total_count || models.length} models (${meta.loadable_count || 0} engine profiles), ${meta.total_size_gb || 0} GB total${filterNote}${downloadNote}`;
      }
    }
    if (path) path.textContent = meta.models_dir || '—';
    if (hint) {
      hint.textContent = typeFilter === 'downloading'
        ? 'Active Hugging Face downloads from Model catalog appear here with live progress.'
        : typeFilter === 'loaded'
          ? 'Only models currently loaded on the GPU. Use Unload on a row to free VRAM.'
          : typeFilter === 'accelerators'
          ? 'DFlash/DSpark draft files only (name contains DFlash or DSpark) — small checkpoints paired with a full target for speculative decoding. Full target GGUFs belong under All models.'
          : typeFilter === 'dflash'
            ? 'Green background = installed. Gold DFlash label = speculative stack. Loaded ribbon + Unload = on GPU now.'
            : 'Green background = installed. Gold DFlash label = speculative stack. Loaded ribbon = on GPU.';
    }
  }

  async function waitForVisionJob(jobId, model) {
    for (let attempt = 0; attempt < 600; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        const data = await api(`/api/hf/download/${encodeURIComponent(jobId)}`);
        const job = data?.job;
        if (job?.status === 'done') {
          toast(`Vision enabled for ${model.label || model.filename || 'model'}`);
          window.DFlashStatusFeed?.note('Vision projector ready', model.label || model.filename || '');
          await refresh({ rebindInspector: true });
          if (window.DFlashServerLive?.refresh) await window.DFlashServerLive.refresh(true);
          return;
        }
        if (job?.status === 'error') {
          toast(job.error || job.post_action_error || 'Vision download failed', false);
          return;
        }
      } catch {
        /* keep polling */
      }
    }
    toast('Vision download is taking longer than expected — check Downloads', false);
  }

  async function setupVision(model) {
    if (!model?.path) {
      toast('No model file path', false);
      return;
    }
    const label = model.label || model.filename || model.id || 'model';
    window.DFlashStatusFeed?.setTransient(`Setting up vision for ${label}…`, {
      secondary: 'Finding projector on Hugging Face',
      ttlMs: 120000,
    });
    try {
      const result = await api('/api/models/vision/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_path: model.path,
          server_id: model.server_id || undefined,
        }),
      });
      if (result.ready || result.wired || result.vision_ready) {
        toast(`Vision enabled for ${label}`);
        await refresh({ rebindInspector: true });
        if (window.DFlashServerLive?.refresh) await window.DFlashServerLive.refresh(true);
        return;
      }
      if (result.job_id) {
        window.DFlashDownloadQueue?.track?.({
          jobId: result.job_id,
          repoId: result.repo_id,
          filename: result.filename,
          label: `Vision · ${label}`,
        }, { navigate: false });
        toast(`Downloading vision projector for ${label}…`);
        void waitForVisionJob(result.job_id, model);
        return;
      }
      toast(result.message || 'Vision setup started');
    } catch (err) {
      toast(err.message, false);
      window.DFlashStatusFeed?.note('Vision setup failed', err.message || label);
    }
  }

  async function selectModel(key, { applyInspector = true } = {}) {
    const model = models.find((entry) => modelKey(entry) === key);
    if (!model) return;
    if (applyInspector && window.DFlashServerLive?.flushInspectorSave) {
      await window.DFlashServerLive.flushInspectorSave();
    }
    selectedKey = key;
    localStorage.setItem('dflashConsole.selectedModelKey', key);
    window.DFlashServerLive?.syncModelPicker?.(key);
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    if (applyInspector && window.DFlashServerLive?.applyModelSelection) {
      await window.DFlashServerLive.applyModelSelection(model);
    }
  }

  async function loadModel(model) {
    if (!window.DFlashServerLive?.loadModelOnServer) {
      toast('Engine panel is not ready yet.', false);
      return;
    }
    if (!model?.path) {
      toast('This file is not available to load.', false);
      return;
    }
    let serverId = model.server_id;
    if (model.plain_gguf || !serverId) {
      const active = window.DFlashServerLive.activeServer?.();
      serverId = active?.id;
      if (!serverId) {
        toast('Select an engine on the Engines tab first', false);
        return;
      }
    }
    window.DFlashStatusFeed?.setTransient(`Loading ${model.label || model.id}…`, {
      secondary: model.server_id ? 'Reading weights into GPU' : 'Loading onto active engine',
      ttlMs: 120000,
    });
    await window.DFlashServerLive.loadModelOnServer(serverId, model);
    await refresh({ rebindInspector: true });
    if (window.DFlashServerLive?.refresh) {
      await window.DFlashServerLive.refresh(true);
    }
  }

  async function refresh({ rebindInspector = false } = {}) {
    const filter = document.getElementById('modelsFilterInput')?.value || '';
    const [data, serversData] = await Promise.all([
      api('/api/models'),
      api('/api/servers').catch(() => ({ servers: [] })),
    ]);
    models = mergeModelsWithState(data.models || [], serversData, loadBrowsePrefs());
    renderFooter(data);
    renderTable(filter, { force: true });
    if (!selectedKey || !models.some((m) => modelKey(m) === selectedKey)) {
      const firstConfigured = models.find((m) => m.loadable);
      if (firstConfigured) await selectModel(modelKey(firstConfigured), { applyInspector: true });
      else if (models[0]) await selectModel(modelKey(models[0]), { applyInspector: true });
    } else if (rebindInspector) {
      await selectModel(selectedKey, { applyInspector: true });
    }
  }

  function normalizeTypeFilter(next, { allowEmptyDownloading = false } = {}) {
    const filter = ['all', 'dflash', 'accelerators', 'downloading', 'loaded'].includes(next) ? next : 'all';
    if (filter === 'downloading' && !allowEmptyDownloading && !getActiveDownloadJobs().length) {
      return 'dflash';
    }
    return filter;
  }

  function setTypeFilter(next, { allowEmptyDownloading = false } = {}) {
    typeFilter = normalizeTypeFilter(next, { allowEmptyDownloading });
    localStorage.setItem(TYPE_FILTER_KEY, typeFilter);
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.modelsFilter === typeFilter);
    });
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    renderFooter(meta);
  }

  function onDownloadQueueUpdate() {
    if (document.body.dataset.activeView !== 'models') return;
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    renderFooter(meta);
    if (typeFilter === 'downloading' && !getActiveDownloadJobs().length) {
      /* keep filter selected; empty state shown */
    }
  }

  function bind() {
    document.getElementById('modelsFilterInput')?.addEventListener('input', (e) => {
      renderTable(e.target.value, { force: true });
    });
    const modelTypePick = document.getElementById('modelsTypeFilter');
    if (modelTypePick) {
      modelTypePick.value = modelTypeFilter;
      modelTypePick.addEventListener('change', (event) => {
        const next = MODEL_TYPE_FILTERS.has(event.target.value) ? event.target.value : 'all';
        modelTypeFilter = next;
        localStorage.setItem(MODEL_TYPE_FILTER_KEY, next);
        renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
      });
    }
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.addEventListener('click', () => setTypeFilter(btn.dataset.modelsFilter, { allowEmptyDownloading: true }));
    });
    setTypeFilter(typeFilter);
    window.DFlashDownloadQueue?.subscribe?.(onDownloadQueueUpdate);

    document.addEventListener('click', hideContextMenu);
    document.addEventListener('scroll', hideContextMenu, true);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideContextMenu();
    });
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      if (pollPaused) return;
      if (document.body.dataset.activeView === 'models') {
        void refresh().catch(() => {});
      }
    }, 5000);
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    const tableWrap = document.querySelector('.lm-view[data-view="models"] .lm-models-table-wrap');
    tableWrap?.addEventListener('mouseenter', () => { pollPaused = true; });
    tableWrap?.addEventListener('mouseleave', () => { pollPaused = false; });
    void refresh({ rebindInspector: true })
      .then(() => {
        if (typeFilter === 'downloading' && !getActiveDownloadJobs().length) {
          setTypeFilter('dflash');
        }
        startPolling();
      })
      .catch((err) => toast(err.message, false));
  });

  window.DFlashModelsLive = { refresh, selectModel, loadModel, setTypeFilter };
})();
