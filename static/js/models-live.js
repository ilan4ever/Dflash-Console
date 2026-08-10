/** Models tab — local catalog + inspector binding */
(function () {
  const { api, toast } = window.ConsoleApi;

  let models = [];
  let meta = {};
  let selectedKey = localStorage.getItem('dflashConsole.selectedModelKey') || '';
  let loadedServerIds = new Set();
  let loadedPathKeys = new Set();
  let loadedModelIds = new Set();
  let bootingServers = {};
  let contextModel = null;
  let stackPreflight = { key: '', status: 'idle', result: null };
  let hfAcceleratorCatalog = [];
  let hfAcceleratorStatus = 'idle';
  let hfAcceleratorRequest = null;
  let hfAcceleratorRevision = 0;

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

  function acceleratorBadge() {
    return '<span class="lm-tag orange" title="Draft accelerator; not a target model">Accelerator</span>';
  }

  function hfAcceleratorBadge() {
    return '<span class="lm-tag blue" title="A compatible DFlash accelerator is listed on Hugging Face">HF accelerator</span>';
  }

  function splitShardBadge(model) {
    const count = Number(model?.split_count || 0);
    return count > 1
      ? `<span class="lm-tag purple" title="This GGUF model is stored across ${count} shard files">${count} shards</span>`
      : '';
  }

  function dflashCompatibilityBadge() {
    return '<span class="lm-tag gold" title="This model has a matching DFlash accelerator and can be converted from the Models tab">DFlash compatible</span>';
  }

  function isDflashConvertible(model) {
    return !!(
      model
      && !isDflashAccelerator(model)
      // Already-DFlash models carry the DFlash logo — no need for the
      // 'DFlash compatible' hint on them.
      && !isDflashModel(model)
      && (
        (
          isDflashStack(model)
          && model.stack_status === 'unregistered'
          && model.draft_path
        )
        || isHfAcceleratorAvailable(model)
      )
    );
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
      if (cap === 'instruct' || cap === 'tools' || cap === 'vision' || cap === 'ar' || cap === 'dflash' || cap === 'reasoning') return;
      tags.push(`<span class="lm-tag blue">${escapeHtml(cap)}</span>`);
    });
    return tags.join('');
  }

  const MODALITY_BADGES = {
    llm: ['LLM', 'blue'],
    embedding: ['Embed', 'purple'],
    'speech-to-text': ['STT', 'green'],
    'text-to-speech': ['TTS', 'green'],
    vision: ['Vision', 'purple'],
    ocr: ['OCR', 'yellow'],
    translation: ['Translate', 'blue'],
  };

  function modalityBadge(model) {
    const modality = String(model?.modality || '').trim().toLowerCase();
    const entry = MODALITY_BADGES[modality];
    return entry
      ? `<span class="lm-tag ${entry[1]}" title="Modality: ${escapeHtml(modality)}">${entry[0]}</span>`
      : '';
  }

  function normalizeModelPath(path) {
    return String(path || '').replace(/\\/g, '/').trim().toLowerCase();
  }

  function serverHasModelOnGpu(server) {
    if (!server) return false;
    if (server.status === 'loaded') return true;
    const loaded = server.loaded_models;
    if (Array.isArray(loaded) && loaded.length > 0) return true;
    const cards = Array.isArray(server.visible_cards) ? server.visible_cards : [];
    return cards.some((card) => {
      const state = String(card?.card_state || '').toLowerCase();
      return state === 'ready' || state === 'loading' || state === 'loaded';
    });
  }

  function collectLoadedMarkers(serversData) {
    const serverIds = new Set();
    const pathKeys = new Set();
    const modelIds = new Set();
    const addPath = (value) => {
      const key = normalizeModelPath(value);
      if (key) pathKeys.add(key);
    };
    const addId = (value) => {
      const id = String(value || '').trim().toLowerCase();
      if (id) modelIds.add(id);
    };

    for (const server of serversData?.servers || []) {
      if (!serverHasModelOnGpu(server)) continue;
      if (server.id) serverIds.add(server.id);
      for (const modelId of server.loaded_models || []) {
        addId(modelId);
        addPath(modelId);
      }
      addId(server.active_model_id);
      addPath(server.model_catalog?.target_path);
      addPath(server.target_path);
      for (const card of server.visible_cards || []) {
        addPath(card?.path || card?.model_path);
        addId(card?.id || card?.model_id);
      }
      for (const layer of server.model_stack || []) {
        if (layer?.role === 'target' || layer?.role === 'draft-dflash') {
          addPath(layer.path);
        }
      }
    }

    for (const row of serversData?.external_gpu_loads || []) {
      addPath(row?.model_path || row?.path);
      addId(row?.model_id || row?.model_name);
    }

    return { serverIds, pathKeys, modelIds };
  }

  function isStackLoadedOnGpu(model) {
    if (!model) return false;
    if (model.runtime_loaded || model.loaded_on_gpu) return true;
    if (model.server_id && loadedServerIds.has(model.server_id)) return true;
    const pathKey = normalizeModelPath(model.path);
    if (pathKey && loadedPathKeys.has(pathKey)) return true;
    const ids = [model.id, model.model_id, model.filename]
      .map((value) => String(value || '').trim().toLowerCase())
      .filter(Boolean);
    return ids.some((id) => loadedModelIds.has(id));
  }

  function isModelReadyToLoad(model) {
    return !!(
      model?.loadable
      && !modelFileMissing(model)
      && !isDflashAccelerator(model)
      && model.stack_status !== 'unregistered'
      && !Number(model?.split_count || 0)
      && !isStackLoadedOnGpu(model)
      && !isStackBooting(model)
    );
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
    const modality = modalityBadge(model);
    const status = isDflashStack(model) ? stackStatusTag(model) : '';
    const compatibility = isDflashConvertible(model) ? dflashCompatibilityBadge() : '';
    const accelerator = isDflashAccelerator(model) ? acceleratorBadge() : '';
    const hfAccelerator = isHfAcceleratorAvailable(model) ? hfAcceleratorBadge() : '';
    const split = splitShardBadge(model);
    const dup = duplicateTag(model);
    const weak = weakMatchTag(model);
    const caps = capabilityTags(model.capabilities, {
      loadable: model.loadable && !isDflashAccelerator(model) && !modelFileMissing(model) && model.stack_status !== 'unregistered',
      port: model.port,
    });
    if (isDflashStack(model)) {
      return modality + status + compatibility + accelerator + hfAccelerator + split + caps + dup + weak;
    }
    return modality + status + compatibility + accelerator + hfAccelerator + split + dup + weak + caps;
  }

  function stackActionButton(model) {
    if (isStackBooting(model)) {
      return '<span class="lm-tag dim">loading…</span>';
    }
    if (isStackLoadedOnGpu(model)) {
      return '<button class="lm-btn ghost tiny" type="button" data-action="unload-model" title="Remove model from GPU">Unload</button>';
    }
    if (isDflashAccelerator(model)) {
      return '<span class="lm-tag orange" title="Accelerators are loaded only with a full target model in a DFlash stack">stack only</span>';
    }
    if (modelFileMissing(model)) {
      return '<span class="lm-tag yellow" title="File not found on disk — refresh the catalog or check model folders">missing file</span>';
    }
    if (model.loadable && model.stack_status !== 'unregistered') {
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
    if (model?.dflash_stack) return true;
    if (!isDflashModel(model)) return false;
    // Stacks need a target/draft pairing (accelerator ready or profile-backed).
    return !!(model.loadable || model.draft_path || model.server_id);
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
    const paths = Array.isArray(model.duplicate_paths) ? model.duplicate_paths : [];
    const uniquePaths = new Set(paths.map((path) => normalizeModelPath(path)).filter(Boolean));
    const identical = model.duplicate_identical !== false;
    const title = identical
      ? `This model has ${count} identical copies on disk. Console shows one preferred entry; no files were deleted.`
      : uniquePaths.size <= 1
        ? 'Same file is listed more than once in the library (for example stack + plain GGUF). Prefer the stack card.'
        : `This exact filename exists in ${count} folders on disk. Pick the copy you want for this stack.`;
    const label = identical ? `${count} copies` : `same name ×${count}`;
    return `<span class="lm-tag yellow" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
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
    const caps = Array.isArray(model.capabilities) ? model.capabilities : [];
    // A real DFlash stack target (dflash_stack, a draft_path, or the 'dflash'
    // capability) is NOT an accelerator. Only bare draft files (e.g.
    // gemma-4-12B-it-DFlash-Q4_K_M.gguf) are stack-only, even when their label
    // contains 'DFlash' (e.g. the unregistered "Gemma 12B DFlash" QAT target).
    if (model.dflash_stack || model.draft_path || caps.includes('dflash')) return false;
    const name = `${model.filename || ''} ${model.label || ''}`.toLowerCase();
    if (!name || name.startsWith('mmproj')) return false;
    return /dflash|dspark/.test(name);
  }

  // External GGUF files that are not already inside the Console's own library
  // can be copied/moved into the Console folder so they register under DFlash
  // Console and are managed by the app.
  function canImportToConsole(model) {
    if (!model?.path) return false;
    if (model.source === 'dflash' || model.source === 'dflash-profile' || model.source === 'dflash-stack') {
      return false;
    }
    if (isDflashAccelerator(model)) return false;
    if (model.loadable && model.server_id) return false;
    return String(model.path).toLowerCase().endsWith('.gguf');
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
    const markers = collectLoadedMarkers(serversData);
    loadedServerIds = markers.serverIds;
    loadedPathKeys = markers.pathKeys;
    loadedModelIds = markers.modelIds;
    bootingServers = {};
    for (const server of serversData.servers || []) {
      serverMap[server.id] = server;
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
          runtime_loaded: serverHasModelOnGpu(server) || isStackLoadedOnGpu(merged),
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
        merged.runtime_loaded = isStackLoadedOnGpu(merged);
      }
      return merged;
    });
  }

  function modelIdentifier(model) {
    return model.model_id || model.id || model.server_id || model.filename || model.path || '';
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

  function canStartHfStack(model, result) {
    return (
      !isDflashAccelerator(model)
      && isHfAcceleratorAvailable(model)
      && ['no-accelerator', 'weak-match'].includes(result?.reason_code)
    );
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
    if (canStartHfStack(model, result)) {
      return `
        <button type="button" data-cmd="create-stack" title="Open the DFlash stack wizard">
          Create DFlash stack…
        </button>
        <div class="df-stack-preflight df-model-stack-preflight is-ready">A matching Hugging Face accelerator is available. Continue in the wizard to download or choose it.</div>`;
    }
    if (result.reason_code === 'path-not-allowed') {
      return `
        <button type="button" data-cmd="create-stack" disabled title="Enable this model folder in Settings first">
          Model folder not enabled
        </button>
        <div class="df-stack-preflight df-model-stack-preflight is-unavailable">${escapeHtml(result.reason)}</div>`;
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
      const message = err.message || 'Could not check this model for DFlash compatibility.';
      const pathNotAllowed = /path not under allowed model directories/i.test(message);
      stackPreflight = {
        key,
        status: 'unavailable',
        result: {
          reason_code: pathNotAllowed ? 'path-not-allowed' : 'check-failed',
          reason: pathNotAllowed
            ? 'This model is scanned from outside the allowed model libraries. Add its folder in Settings → model libraries before creating a DFlash stack.'
            : message,
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
    if (!model?.path || model?.path_missing) return '';
    return '<span class="lm-badge installed">Installed</span>';
  }

  function modelFileMissing(model) {
    return !!(model?.path_missing || (model?.path && !model.path));
  }

  function installedDflashLogo(model) {
    if (!model?.path || model?.path_missing || !isDflashModel(model)) return '';
    return dflashLogoLabel('DFlash');
  }

  function modelHasReasoning(model) {
    if (model?.reasoning === true) return true;
    const caps = Array.isArray(model?.capabilities) ? model.capabilities : [];
    return caps.includes('reasoning');
  }

  function reasoningBadge(model) {
    if (!modelHasReasoning(model)) return '';
    return '<span class="lm-badge reasoning" title="This model exposes a thinking/reasoning mode">Reasoning</span>';
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
    'hf-accelerator',
  ]);
  let modelTypeFilter = localStorage.getItem(MODEL_TYPE_FILTER_KEY) || 'all';
  if (!MODEL_TYPE_FILTERS.has(modelTypeFilter)) modelTypeFilter = 'all';

  let pollTimer = null;
  let pollPaused = false;
  let lastRenderSignature = '';

  function modelType(model) {
    const modality = String(model?.modality || '').trim().toLowerCase();
    if (MODALITY_BADGES[modality]) return modality;
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

  function hfTargetText(model) {
    const pathName = String(model?.path || '').split(/[/\\]/).pop();
    return [model?.filename, model?.label, pathName].filter(Boolean).join(' ');
  }

  function hfIdentityTokens(value) {
    return new Set(
      String(value || '')
        .toLowerCase()
        .replace(/\b(?:dflash|dspark|gguf|draft|model|speculative|decoding|quantized|llama|cpp|instruct|chat|it)\b/g, ' ')
        .replace(/\b(?:q\d+(?:_[a-z0-9]+)*|iq\d+(?:_[a-z0-9]+)*|f16|f32|bf16)\b/g, ' ')
        .replace(/[^a-z0-9.]+/g, ' ')
        .split(/\s+/)
        .filter((token) => token.length > 1),
    );
  }

  function hfFamily(value) {
    const text = String(value || '').toLowerCase();
    for (const [family, pattern] of [
      ['deepseek', /deepseek/],
      ['qwen', /qwen/],
      ['gemma', /gemma/],
      ['bonsai', /bonsai/],
      ['ornith', /ornith/],
      ['laguna', /laguna/],
      ['llama', /llama/],
      ['mistral', /mistral/],
      ['phi', /(?:^|[^a-z])phi(?:[^a-z]|$)/],
      ['gpt', /(?:^|[^a-z])gpt(?:[^a-z]|$)/],
    ]) {
      if (pattern.test(text)) return family;
    }
    return '';
  }

  function hfParameter(value) {
    const match = String(value || '').match(/(?:^|[^a-z])(\d+(?:\.\d+)?)\s*b(?:\b|[^a-z])/i);
    return match ? `${match[1].toLowerCase()}b` : '';
  }

  function hfAcceleratorMatchesTarget(target, accelerator) {
    if (!target || isDflashAccelerator(target)) return false;
    const targetText = hfTargetText(target);
    const acceleratorText = [
      accelerator?.id,
      accelerator?.title,
      accelerator?.label,
      ...(Array.isArray(accelerator?.tags) ? accelerator.tags : []),
    ].filter(Boolean).join(' ');
    if (!/dflash|dspark/i.test(acceleratorText)) return false;

    const targetFamily = hfFamily(targetText);
    const acceleratorFamily = hfFamily(acceleratorText);
    if (targetFamily && acceleratorFamily && targetFamily !== acceleratorFamily) return false;

    const targetParam = hfParameter(targetText);
    const acceleratorParam = hfParameter(acceleratorText);
    if (targetParam && acceleratorParam && targetParam !== acceleratorParam) return false;

    const targetTokens = hfIdentityTokens(targetText);
    const acceleratorTokens = hfIdentityTokens(acceleratorText);
    const overlap = [...targetTokens].filter((token) => acceleratorTokens.has(token)).length;
    return overlap >= 2;
  }

  function isHfAcceleratorAvailable(model) {
    if (hfAcceleratorStatus !== 'ready' || isDflashAccelerator(model)) return false;
    return hfAcceleratorCatalog.some((accelerator) => hfAcceleratorMatchesTarget(model, accelerator));
  }

  function ensureHfAcceleratorCatalog() {
    if (hfAcceleratorStatus === 'ready') return Promise.resolve();
    if (hfAcceleratorRequest) return hfAcceleratorRequest;

    hfAcceleratorStatus = 'loading';
    hfAcceleratorRevision += 1;
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    hfAcceleratorRequest = api('/api/hf/search?q=dflash&limit=50&category=dflash', { timeoutMs: 45000 })
      .then((data) => {
        if (!data?.success) throw new Error(data?.error || 'Hugging Face search failed');
        hfAcceleratorCatalog = Array.isArray(data.models) ? data.models : [];
        hfAcceleratorStatus = 'ready';
      })
      .catch((error) => {
        hfAcceleratorCatalog = [];
        hfAcceleratorStatus = 'error';
        if (modelTypeFilter === 'hf-accelerator') {
          toast(`Could not check Hugging Face accelerators: ${error.message}`, false);
        }
      })
      .finally(() => {
        hfAcceleratorRequest = null;
        hfAcceleratorRevision += 1;
        if (modelTypeFilter === 'hf-accelerator' || document.body.dataset.activeView === 'models') {
          renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
        }
      });
    return hfAcceleratorRequest;
  }

  function matchesModelType(model) {
    if (modelTypeFilter === 'hf-accelerator') return isHfAcceleratorAvailable(model);
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
    return `${typeFilter}:${modelTypeFilter}:${hfAcceleratorRevision}:${selectedKey}:${needle}:${rows}`;
  }

  // Provider source label (e.g. 'LM Studio', 'DFlash') for the Source column.
  function modelSourceLabel(model) {
    const source = String(model?.source || '').trim().toLowerCase();
    if (source === 'lmstudio') return 'LM Studio';
    if (source === 'dflash' || source === 'dflash-profile' || source === 'dflash-stack') return 'DFlash';
    if (source === 'local' || source === 'library' || !source) return 'Local';
    if (source === 'other' || source === 'unknown') return 'Other';
    return String(model.source).replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // Source cell: provider label, with the HF publisher kept as a muted suffix.
  function modelSourceCell(model) {
    const label = modelSourceLabel(model);
    const publisher = String(model?.publisher || '').trim();
    if (publisher && publisher.toLowerCase() !== label.toLowerCase()) {
      return `${escapeHtml(label)}<span class="lm-col-sub"> · ${escapeHtml(publisher)}</span>`;
    }
    return escapeHtml(label);
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
    const visibleDownloads = modelTypeFilter === 'hf-accelerator' ? [] : activeDownloads;
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

    if (!catalogRows.length && !(typeFilter === 'loaded' ? [] : visibleDownloads).length) {
      const emptyLabel = modelTypeFilter === 'hf-accelerator'
        ? hfAcceleratorStatus === 'loading'
          ? 'Checking Hugging Face for compatible DFlash accelerators…'
          : hfAcceleratorStatus === 'error'
            ? 'Hugging Face could not be checked. Try this filter again when online.'
            : 'No local target models have a matching accelerator listed on Hugging Face.'
        : typeFilter === 'dflash'
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
      ...(typeFilter === 'loaded' ? [] : visibleDownloads.map((job) => renderDownloadingRow(job))),
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
            <div class="lm-model-title-line"><span class="lm-model-title-text">${escapeHtml(modelTitleLine(model))}</span>${installedBadge(model)}${reasoningBadge(model)}${installedDflashLogo(model)}${pinMark}</div>
            <div class="lm-model-tags-line">${capTags(model)}</div>
            ${modelPathHint(model)}
            ${draftHint(model)}
          </td>
          <td class="lm-col-meta">${escapeHtml(model.arch || '—')}</td>
          <td class="lm-col-meta">${escapeHtml(model.params || '—')}</td>
          <td class="lm-col-meta">${modelSourceCell(model)}</td>
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
        if (model?.loadable && !isDflashAccelerator(model)) void loadModel(model);
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
        await window.DFlashServerLive.refresh(true, { fresh: true });
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
      ${canImportToConsole(model) ? `<button type="button" data-cmd="copy-to-console">Copy to DFlash Console</button>
      <button type="button" data-cmd="move-to-console">Move to DFlash Console</button>` : ''}
      <hr>
      <div id="modelsStackAction">${stackMenuActionHtml(model)}</div>
      ${isDflashAccelerator(model)
        ? '<div class="df-stack-preflight df-model-stack-preflight is-unavailable">Accelerators are loaded only with a full target model.</div>'
        : `<button type="button" data-cmd="load"${model.loadable ? '' : ' disabled'}>Load to Server</button>`}
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
    if (!isDflashAccelerator(model) && hfAcceleratorStatus !== 'ready') {
      void ensureHfAcceleratorCatalog().then(() => {
        if (contextModel === model) updateStackMenuAction(model);
      });
    }
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
      const hfStack = canStartHfStack(model, check);
      if ((stackPreflight.status !== 'ready' || !check?.eligible) && !hfStack) {
        toast(check?.reason || 'This model is not ready for a DFlash stack', false);
        return;
      }
      const accel = isDflashAccelerator(model);
      window.DFlashStackWizard?.open?.({
        targetPath: accel ? '' : model.path,
        targetLabel: accel ? '' : (model.label || model.filename),
        draftPath: accel ? model.path : '',
        draftLabel: accel ? (model.filename || model.label) : '',
        allowHfAccelerator: hfStack,
      });
      return;
    }
    if (cmd === 'load') {
      if (isDflashAccelerator(model)) {
        toast('Choose the full target model; accelerators are stack-only.', false);
        return;
      }
      if (model.loadable) await loadModel(model);
      return;
    }
    if (cmd === 'copy-to-console' || cmd === 'move-to-console') {
      const isMove = cmd === 'move-to-console';
      const name = model.filename || model.label || 'this model';
      if (isMove && !window.confirm(`Move ${name} into the DFlash Console library? The file will be removed from its current location.`)) {
        return;
      }
      if (!canImportToConsole(model)) {
        toast('This model cannot be imported into the Console library', false);
        return;
      }
      try {
        const data = await api('/api/models/import-into-console', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: model.path, mode: isMove ? 'move' : 'copy' }),
        });
        toast(`${isMove ? 'Moved' : 'Copied'} ${name} into DFlash Console library`);
        await refresh({ rebindInspector: true });
        if (window.DFlashServerLive?.refresh) await window.DFlashServerLive.refresh();
        if (data?.library_path && selectedKey === key) selectedKey = '';
      } catch (err) {
        toast(err.message || 'Could not import model into Console library', false);
      }
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
      const cacheNote = meta.cached
        ? (meta.stale ? 'Showing the last saved list while the library refreshes in the background. ' : 'Showing the saved list while checking for library changes. ')
        : '';
      hint.textContent = cacheNote + (typeFilter === 'downloading'
        ? 'Active Hugging Face downloads from Model catalog appear here with live progress.'
        : typeFilter === 'loaded'
          ? 'Only models currently on the GPU (matched by engine profile or file path). Use Unload to free VRAM.'
          : typeFilter === 'accelerators'
          ? 'DFlash/DSpark draft files only (name contains DFlash or DSpark) — small checkpoints paired with a full target for speculative decoding. Full target GGUFs belong under All models.'
          : typeFilter === 'dflash'
            ? 'DFlash stacks with target + accelerator pairing. Green = ready. Gold label = speculative stack.'
            : 'All discovered GGUF files from Console libraries and common model folders on this PC.');
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
    if (isDflashAccelerator(model)) {
      toast('Choose the full target model; accelerators are stack-only.', false);
      return;
    }
    if (modelFileMissing(model)) {
      toast('Model file not found on disk. Refresh the catalog or check Settings → model folders.', false);
      return;
    }
    if (model.stack_status === 'unregistered') {
      toast('Set up a DFlash stack first, or use Load LLM from the row menu.', false);
      return;
    }
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
      await window.DFlashServerLive.refresh(true, { fresh: true });
    }
  }

  async function fetchServersForLibrary() {
    try {
      return await api('/api/servers?include_external=true', { timeoutMs: 20000 });
    } catch (_err) {
      try {
        return await api('/api/servers?include_external=false', { timeoutMs: 12000 });
      } catch (_err2) {
        return { servers: [], external_gpu_loads: [] };
      }
    }
  }

  async function refresh({ rebindInspector = false, forceCatalogRefresh = false } = {}) {
    const filter = document.getElementById('modelsFilterInput')?.value || '';
    const modelsPath = forceCatalogRefresh ? '/api/models?refresh=1' : '/api/models';
    const [data, serversData] = await Promise.all([
      api(modelsPath, { timeoutMs: 45000 }),
      fetchServersForLibrary(),
    ]);
    models = mergeModelsWithState(data.models || [], serversData, loadBrowsePrefs());
    renderFooter(data);
    renderTable(filter, { force: true });
    if (!selectedKey || !models.some((m) => modelKey(m) === selectedKey)) {
      const firstConfigured = models.find((m) => m.loadable && !isDflashAccelerator(m));
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
    const previous = typeFilter;
    typeFilter = normalizeTypeFilter(next, { allowEmptyDownloading });
    if (typeFilter === 'accelerators' && modelTypeFilter !== 'all') {
      // HF accelerator availability applies to target models, never to draft files.
      modelTypeFilter = 'all';
      localStorage.setItem(MODEL_TYPE_FILTER_KEY, modelTypeFilter);
      const modelTypePick = document.getElementById('modelsTypeFilter');
      if (modelTypePick) modelTypePick.value = modelTypeFilter;
    }
    localStorage.setItem(TYPE_FILTER_KEY, typeFilter);
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.modelsFilter === typeFilter);
    });
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    renderFooter(meta);
    if (typeFilter === 'all' && previous !== 'all') {
      void refresh({ forceCatalogRefresh: true }).catch(() => {});
    } else if (typeFilter === 'loaded') {
      void refresh().catch(() => {});
    }
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
        if (next === 'hf-accelerator' && typeFilter !== 'all') {
          typeFilter = 'all';
          localStorage.setItem(TYPE_FILTER_KEY, typeFilter);
          document.querySelectorAll('[data-models-filter]').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.modelsFilter === typeFilter);
          });
        }
        renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
        if (next === 'hf-accelerator') void ensureHfAcceleratorCatalog();
      });
    }
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.addEventListener('click', () => setTypeFilter(btn.dataset.modelsFilter, { allowEmptyDownloading: true }));
    });
    setTypeFilter(typeFilter);
    void ensureHfAcceleratorCatalog();
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

  window.DFlashModelsLive = { refresh, selectModel, loadModel, setTypeFilter, modelHasReasoning };
})();
