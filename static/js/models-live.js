/** Models tab — local catalog + inspector binding */
(function () {
  const { api, toast } = window.ConsoleApi;

  let models = [];
  let meta = {};
  // True while the model catalog is being fetched (first load) so the table
  // shows a loading row instead of the misleading "No models match this filter."
  let catalogLoading = true;
  let selectedKey = localStorage.getItem('dflashConsole.selectedModelKey') || '';
  let loadedServerIds = new Set();
  let loadedPathKeys = new Set();
  let loadedModelIds = new Set();
  let externalGpuLoads = [];
  let bootingServers = {};
  const pendingModelLoads = new Map();
  const pendingServerLoads = new Set();
  const pendingModelUnloads = new Map();
  const pendingServerUnloads = new Set();
  let runtimePollTimer = null;
  let catalogRefreshInFlight = null;
  let partialRetryTimer = null;
  let contextModel = null;
  let stackPreflight = { key: '', status: 'idle', result: null };
  let hfAcceleratorCatalog = [];
  let hfAcceleratorStatus = 'idle';
  let hfAcceleratorRequest = null;
  let hfAcceleratorRevision = 0;
  const suppressedLibrary = { keys: new Set(), paths: new Set() };

  const LOAD_ENGINE_KEY = 'dflashConsole.loadEngine';
  const HF_LOAD_ENGINES = ['transformers', 'vllm', 'freetoken'];
  const LOAD_ENGINE_PREFERENCE = ['transformers', 'vllm', 'dflash'];
  const PINNED_KEY = 'dflashConsole.pinnedModels';
  const LOCAL_CATALOG_CACHE_KEY = 'dflashConsole.modelLibraryCache';
  const MODEL_LIST_REFRESH_MS = 5 * 60 * 1000;

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Normalize a search string so HF/name separators are interchangeable:
  // `baidu/Unlimited-OCR` (Hugging Face style) also matches a local file
  // `baidu.Unlimited-OCR.Q3_K_M` (dots), `baidu-unlimited-ocr`, or spaces.
  function normalizeSearchText(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[/._\-\s\\]+/g, '');
  }

  function modelKey(model) {
    if (model?.server_id) return String(model.server_id);
    const pathKey = normalizeModelPath(model?.path);
    if (model?.library_file && pathKey) return `library-file::${pathKey}`;
    const id = String(model?.id || '').trim();
    if (id && pathKey && id.startsWith('library-file:')) return `${id}::${pathKey}`;
    return id || pathKey || '';
  }

  function modelForRow(row) {
    if (!row) return null;
    const key = row.dataset.modelKey;
    if (key) {
      const byKey = models.find((entry) => modelKey(entry) === key);
      if (byKey) return byKey;
    }
    const modelId = String(row.dataset.modelId || '').trim();
    if (modelId) {
      const byId = models.find((entry) => String(entry.id || '') === modelId);
      if (byId) return byId;
    }
    return null;
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

  function loadCachedModelCatalog() {
    try {
      const cached = JSON.parse(localStorage.getItem(LOCAL_CATALOG_CACHE_KEY) || 'null');
      if (
        !cached
        || cached.version !== 1
        || !Array.isArray(cached.models)
        || !cached.models.length
      ) {
        return null;
      }
      return cached;
    } catch {
      return null;
    }
  }

  function saveCachedModelCatalog(data) {
    if (!data || !Array.isArray(data.models) || !data.models.length) return;
    try {
      const { models: _models, ...footer } = data;
      localStorage.setItem(LOCAL_CATALOG_CACHE_KEY, JSON.stringify({
        version: 1,
        saved_at: Date.now(),
        models: data.models,
        footer,
      }));
    } catch {
      /* A full browser cache must never block the model library. */
    }
  }

  function dflashLogoLabel(label = 'DFlash 1') {
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
    if (model?.incomplete && Number(model?.shard_total || 0) > 1) {
      const present = Number(model.shard_present || 0);
      const total = Number(model.shard_total || 0);
      return `<span class="lm-tag yellow" title="Only ${present} of ${total} weight files are on disk — download is incomplete">Incomplete ${present}/${total}</span>`;
    }
    const count = Number(model?.split_count || 0);
    return count > 1
      ? `<span class="lm-tag purple" title="This GGUF model is stored across ${count} shard files">${count} shards</span>`
      : '';
  }

  function formatModelDiskSize(model) {
    const onDisk = formatSizeGb(model?.size_gb);
    if (model?.incomplete) {
      const expected = formatSizeGb(model?.expected_size_gb);
      if (onDisk !== '—' && expected !== '—') return `${onDisk} / ${expected}`;
      if (onDisk !== '—') return `${onDisk} (incomplete)`;
    }
    return onDisk;
  }

  function dflashCompatibilityBadge() {
    return '<span class="lm-tag gold" title="This model has a matching DFlash accelerator and can be converted from the Models tab">DFlash compatible</span>';
  }

  function isNonDflashStackTarget(model) {
    const path = String(model?.path || model?.filename || '').toLowerCase();
    if (/translategemma|(?:^|[\\/])mtp-/.test(path)) return true;
    const modality = String(model?.modality || '').toLowerCase();
    return modality === 'translation' || modality === 'projector';
  }

  function isDflashConvertible(model) {
    return !!(
      model
      && !isNonDflashStackTarget(model)
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

  function resolveModelPort(model) {
    const direct = Number(model?.port);
    if (Number.isFinite(direct) && direct > 0) return direct;
    const boundId = String(model?.bound_profile_id || model?.server_id || '').trim();
    if (boundId) {
      const boundPort = Number(serverPortById[boundId]);
      if (Number.isFinite(boundPort) && boundPort > 0) return boundPort;
      const profile = models.find((row) => row.server_id === boundId);
      const profilePort = Number(profile?.port);
      if (Number.isFinite(profilePort) && profilePort > 0) return profilePort;
    }
    const pathKey = normalizeModelPath(model?.path);
    if (pathKey) {
      const match = models.find((row) => row.server_id && normalizeModelPath(row.path) === pathKey);
      const matchedPort = Number(match?.port);
      if (Number.isFinite(matchedPort) && matchedPort > 0) return matchedPort;
    }
    return 0;
  }

  function capabilityTags(caps, { loadable = false, port = 0 } = {}) {
    const list = Array.isArray(caps) ? caps : [];
    const tags = [];
    if (loadable) {
      const portNum = Number(port);
      if (Number.isFinite(portNum) && portNum > 0) {
        tags.push(`<span class="lm-badge blue" title="OpenAI-compatible API port for this engine profile">port ${portNum}</span>`);
      }
    }
    if (list.includes('tools')) tags.push('<span class="lm-badge green">tools</span>');
    if (list.includes('vision')) tags.push('<span class="lm-badge purple">vision</span>');
    if (list.includes('ar')) tags.push('<span class="lm-badge blue">AR</span>');
    list.forEach((cap) => {
      if (cap === 'llm' || cap === 'instruct' || cap === 'tools' || cap === 'vision' || cap === 'ar' || cap === 'dflash' || cap === 'reasoning' || cap === 'projector') return;
      tags.push(`<span class="lm-badge blue">${escapeHtml(cap)}</span>`);
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
    projector: ['Projector', 'violet'],
  };

  function modalityBadge(model) {
    const modality = window.DFlashModelCard?.modality?.(model)
      || String(model?.modality || '').trim().toLowerCase();
    const entry = MODALITY_BADGES[modality];
    return entry
      ? `<span class="lm-badge ${entry[1]}" title="Modality: ${escapeHtml(modality)}">${entry[0]}</span>`
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

    const tf = serversData?.transformers_runtime;
    if (tf?.active_model) {
      addPath(tf.active_model);
      addId('glm-ocr');
      addId('glmocr');
    }
    const vllm = serversData?.vllm_runtime;
    if (vllm?.active_model) {
      addPath(vllm.active_model);
    }

    return { serverIds, pathKeys, modelIds };
  }

  function findExternalGpuLoad(model) {
    if (!model || !externalGpuLoads.length) return null;
    const pathKey = normalizeModelPath(model.path);
    const filename = String(model.filename || model.label || '').trim().toLowerCase();
    const stem = filename.replace(/\.gguf$/i, '');
    const ids = new Set([
      String(model.id || '').trim().toLowerCase(),
      String(model.model_id || '').trim().toLowerCase(),
      filename,
      stem,
    ].filter(Boolean));

    for (const row of externalGpuLoads) {
      if (String(row?.card_state || '').toLowerCase() === 'loading') continue;
      const rowPath = normalizeModelPath(row?.model_path || row?.path);
      if (pathKey && rowPath && pathKey === rowPath) return row;
      const rowFile = rowPath ? rowPath.split('/').pop() : '';
      if (filename && rowFile && filename === rowFile) return row;
      const rowName = String(row.model_name || row.title || row.model_id || '').trim().toLowerCase();
      if (stem && rowName && (stem === rowName || stem.includes(rowName) || rowName.includes(stem))) return row;
      if (filename && rowFile && (filename.includes(rowFile) || rowFile.includes(stem))) return row;
      for (const id of ids) {
        if (!id) continue;
        if (id === String(row.model_id || '').trim().toLowerCase()) return row;
        if (id === rowName) return row;
      }
    }
    return null;
  }

  function isStackLoadedOnGpu(model) {
    if (!model) return false;
    if (model.runtime_loaded || model.loaded_on_gpu) return true;
    if (model.server_id && loadedServerIds.has(model.server_id)) return true;
    const pathKey = normalizeModelPath(model.path);
    if (pathKey && loadedPathKeys.has(pathKey)) return true;
    const ids = [model.id, model.model_id, model.filename, model.ollama_model]
      .map((value) => String(value || '').trim().toLowerCase())
      .filter(Boolean);
    return ids.some((id) => loadedModelIds.has(id));
  }

  function isModelReadyToLoad(model) {
    return !!(
      model?.loadable
      && !model?.incomplete
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

  function isModelPendingLoad(model) {
    const key = modelKey(model);
    if (pendingModelLoads.has(key)) return true;
    const serverId = model?.server_id || pendingModelLoads.get(key)?.serverId;
    if (serverId && pendingServerLoads.has(serverId)) return true;
    return false;
  }

  function isModelPendingUnload(model) {
    const key = modelKey(model);
    if (pendingModelUnloads.has(key)) return true;
    const serverId = model?.server_id || pendingModelUnloads.get(key)?.serverId;
    if (serverId && pendingServerUnloads.has(serverId)) return true;
    return false;
  }

  function isStackBooting(model) {
    return isModelPendingLoad(model)
      || !!(model.server_id && bootingServers[model.server_id])
      || isFreeTokenWarmingModel(model);
  }

  function isFreeTokenWarmingModel(model) {
    const boot = bootingServers.freetoken;
    if (!boot) return false;
    const path = normalizeModelPath(model?.path);
    const bootPath = normalizeModelPath(boot.path);
    if (path && bootPath) return path === bootPath;
    return Boolean(path || bootPath);
  }

  function isStackUnloading(model) {
    return isModelPendingUnload(model);
  }

  function loadedRibbon(_model) {
    return '';
  }

  function modelRowClassName(model, { selected = false, pinned = false } = {}) {
    const parts = ['lm-model-row'];
    if (selected) parts.push('selected');
    if (pinned) parts.push('pinned');
    if (isStackUnloading(model)) parts.push('unloading-on-server');
    else if (isStackBooting(model)) parts.push('loading-on-server');
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

  function isProjectorModel(model) {
    if (typeof window.DFlashModelCard?.isProjector === 'function') {
      return window.DFlashModelCard.isProjector(model);
    }
    return model?.is_projector === true || (Array.isArray(model?.capabilities) && model.capabilities.includes('projector'));
  }

  function capTags(model, visibleCount) {
    const classification = window.DFlashModelCard?.classificationTags?.(model)
      || modalityBadge(model);
    const status = isDflashStack(model) ? stackStatusTag(model) : '';
    const role = modelCatalogRoleTag(model);
    const location = (model.duplicate_group || model.library_file) ? modelLocationTag(model) : '';
    const compatibility = isDflashConvertible(model) ? dflashCompatibilityBadge() : '';
    const hfAccelerator = isHfAcceleratorAvailable(model) ? hfAcceleratorBadge() : '';
    const split = splitShardBadge(model);
    const dup = duplicateTag(model, visibleCount);
    const weak = weakMatchTag(model);
    const ext = needsExternalTag(model)
      ? '<span class="lm-tag external-tag" title="This model lives outside the DFlash Console library (another app or a scanned folder)">External</span>'
      : '';
    const capList = Array.isArray(model.capabilities) ? [...model.capabilities] : [];
    const modality = window.DFlashModelCard?.modality?.(model) || String(model?.modality || '').toLowerCase();
    if (modality === 'vision' || modality === 'projector' || isProjectorModel(model)) {
      const visionIdx = capList.indexOf('vision');
      if (visionIdx >= 0) capList.splice(visionIdx, 1);
    }
    const caps = capabilityTags(capList, {
      loadable: model.loadable && !isDflashAccelerator(model) && !isProjectorModel(model) && !modelFileMissing(model) && model.stack_status !== 'unregistered',
      port: resolveModelPort(model),
    });
    if (isDflashStack(model)) {
      return classification + ext + role + location + status + compatibility + hfAccelerator + split + caps + dup + weak;
    }
    return classification + ext + role + location + status + compatibility + hfAccelerator + split + dup + weak + caps;
  }

  function actionStackHtml(buttons, { setup = false } = {}) {
    const cls = setup ? 'lm-action-stack is-setup-row' : 'lm-action-stack';
    return `<div class="${cls}">${buttons.join('')}</div>`;
  }

  function actionButton(action, label, title, extraClass = '') {
    const cls = ['lm-btn', 'ghost', 'tiny', 'lm-action-btn', extraClass].filter(Boolean).join(' ');
    return `<button class="${cls}" type="button" data-action="${action}" title="${escapeHtml(title)}">${escapeHtml(label)}</button>`;
  }

  let autoPickedLoadEngine = '';

  function getLoadEngine() {
    const saved = String(localStorage.getItem(LOAD_ENGINE_KEY) || '').toLowerCase();
    if (['dflash', 'vllm', 'transformers', 'freetoken'].includes(saved)) return saved;
    if (autoPickedLoadEngine) return autoPickedLoadEngine;
    return 'dflash';
  }

  function setLoadEngine(id) {
    const next = ['dflash', 'vllm', 'transformers', 'freetoken'].includes(id) ? id : 'dflash';
    localStorage.setItem(LOAD_ENGINE_KEY, next);
    document.querySelectorAll('[data-load-engine-pick]').forEach((el) => {
      el.value = next;
    });
    window.dispatchEvent(new CustomEvent('dflash-load-engine', { detail: next }));
  }

  function isHfEngineModel(model) {
    if (!model) return false;
    const engines = Array.isArray(model.engines) ? model.engines : [];
    const runtimeId = String(model.runtime_id || '');
    if (engines.includes('vllm') || engines.includes('transformers') || engines.includes('freetoken')) return true;
    if (runtimeId === 'vllm' || runtimeId === 'transformers' || runtimeId === 'freetoken') return true;
    return model.kind === 'dir' && String(model.modality || 'llm') === 'llm';
  }

  function syncLoadEnginePicks() {
    const current = getLoadEngine();
    document.querySelectorAll('[data-load-engine-pick]').forEach((el) => {
      el.value = current;
    });
  }

  function hfEnginePicker(model) {
    if (!isHfEngineModel(model)) return '';
    const engines = Array.isArray(model?.engines) && model.engines.length
      ? HF_LOAD_ENGINES.filter((id) => model.engines.includes(id)).concat(
        model.engines.filter((id) => !HF_LOAD_ENGINES.includes(id)),
      )
      : HF_LOAD_ENGINES.slice();
    const preferred = engines.includes('transformers') ? 'transformers' : engines[0];
    const global = getLoadEngine();
    const current = engines.includes(global) ? global : preferred;
    const opts = engines.map((id) => {
      const label = id === 'vllm'
        ? 'vLLM'
        : id === 'transformers'
          ? 'Transformers'
          : id === 'freetoken'
            ? 'FreeToken (WSL)'
            : id;
      return `<option value="${escapeHtml(id)}" ${id === current ? 'selected' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
    return `<select class="lm-select small lm-engine-pick" data-engine-pick="${escapeHtml(modelKey(model))}" title="Engine to load this model">${opts}</select>`;
  }

  function rowEngineControl(model) {
    const picker = hfEnginePicker(model);
    if (picker) return picker;
    if (!canLoadInConsole(model) || needsDflashSetupActions(model) || isDflashAccelerator(model)) return '';
    const engine = getLoadEngine();
    const label = engine === 'vllm'
      ? 'vLLM'
      : engine === 'transformers'
        ? 'Transformers'
        : engine === 'freetoken'
          ? 'FreeToken (WSL)'
          : 'DFlash';
    return `<span class="lm-engine-pick-label" title="Uses ${label} when you load this model">${escapeHtml(label)}</span>`;
  }

  function canLoadInConsole(model) {
    if (!model?.path || modelFileMissing(model)) return false;
    if (isProjectorModel(model)) return false;
    if (isDflashAccelerator(model)) return false;
    if (Number(model.split_count || 0) > 1) return false;
    return true;
  }

  function needsDflashSetupActions(model) {
    return !!(
      model?.path
      && model.draft_path
      && model.stack_status === 'unregistered'
      && !model.server_id
      && !isDflashAccelerator(model)
      && !modelFileMissing(model)
    );
  }

  function stackActionButton(model) {
    if (isStackUnloading(model)) {
      return '<span class="lm-tag orange lm-model-load-pill" title="Unloading model from GPU">Unloading…</span>';
    }
    if (isStackBooting(model)) {
      const boot = bootingServers[model.server_id] || (isFreeTokenWarmingModel(model) ? bootingServers.freetoken : null);
      const progress = boot?.progress;
      const pct = progress != null && Number.isFinite(Number(progress))
        ? ` ${Math.round(Number(progress))}%`
        : '';
      const warming = Boolean(bootingServers.freetoken && isFreeTokenWarmingModel(model));
      const label = warming ? 'Warming' : 'Loading';
      const title = escapeHtml(boot?.detail || `${label} model onto GPU`);
      return `<span class="lm-tag blue lm-model-load-pill" title="${title}">${label}${pct}…</span>`;
    }
    if (isStackLoadedOnGpu(model)) {
      return actionButton('unload-model', 'Unload', 'Remove model from GPU', 'lm-btn-unload-active');
    }
    if (isDflashAccelerator(model)) {
      return '<span class="lm-tag orange" title="Accelerators are loaded only with a full target model in a DFlash stack">stack only</span>';
    }
    if (isProjectorModel(model)) {
      return '<span class="lm-tag violet" title="Vision projector companion — load the main model; this file is wired automatically">projector only</span>';
    }
    if (modelFileMissing(model)) {
      return '<span class="lm-tag yellow" title="File not found on disk — refresh the catalog or check model folders">missing file</span>';
    }
    if (model.stack_status === 'disabled' && model.server_id) {
      return actionButton('enable-stack', 'Enable', 'Enable this engine profile');
    }
    if (needsDflashSetupActions(model)) {
      return actionStackHtml([
        actionButton('setup-stack', 'Setup stack', 'Pair the target model with its accelerator'),
        actionButton('load-llm', 'Load LLM', 'Load the target model without speculative decoding'),
      ], { setup: true });
    }
    if (canLoadInConsole(model)) {
      const control = rowEngineControl(model);
      if (control) {
        return actionStackHtml([control, actionButton('load-model', 'Load', 'Load model onto GPU')]);
      }
      return actionButton('load-model', 'Load', 'Load model onto GPU');
    }
    if (model?.path) {
      return actionButton('open-folder', 'Open', 'Show file in Explorer');
    }
    return '<span class="lm-tag dim" title="No local file">no file</span>';
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
    if (isStackUnloading(model)) {
      return '<span class="lm-tag orange">unloading</span>';
    }
    if (isStackBooting(model)) {
      return '<span class="lm-tag blue">loading</span>';
    }
    if (isStackLoadedOnGpu(model)) {
      return '';
    }
    if (model.stack_status === 'disabled') {
      return '<span class="lm-tag yellow">disabled profile</span>';
    }
    if (model.stack_status === 'unregistered') {
      return '<span class="lm-tag blue">needs setup</span>';
    }
    return '';
  }

  function duplicateTag(model, visibleCount) {
    if (!model.duplicate_group) return '';
    const uniquePaths = [...new Set(
      (Array.isArray(model.duplicate_paths) ? model.duplicate_paths : [])
        .map((value) => normalizeModelPath(value))
        .filter(Boolean),
    )];
    const count = uniquePaths.length || model.duplicate_count || 2;
    const identical = model.duplicate_identical !== false;
    if (identical) {
      const title = `This model has ${count} identical copies on disk. Console shows one preferred entry; no files were deleted.`;
      return `<span class="lm-tag yellow" title="${escapeHtml(title)}">${count} copies</span>`;
    }
    const shown = Math.max(0, Number(visibleCount) || 0);
    if (shown < 2 && count < 2) return '';
    const pathLines = uniquePaths.map((value) => `• ${value.replace(/\//g, '\\')}`).join('\n');
    const title = shown >= 2
      ? `Same filename in ${count} folder${count === 1 ? '' : 's'} on this PC. ${shown} rows are listed below — each path is a separate file you can import or delete.\n\n${pathLines}`
      : `Same filename in ${count} folder${count === 1 ? '' : 's'} on this PC.\n\n${pathLines}`;
    const label = shown >= 2 ? `same name ×${shown}` : `${count} locations`;
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

  function modelLocationTag(model) {
    const path = String(model?.path || '').replace(/\\/g, '/');
    if (!path) return '';
    const parts = path.split('/').filter(Boolean);
    if (parts.length < 2) return '';
    const folder = parts[parts.length - 2];
    return `<span class="lm-tag violet" title="On disk: ${escapeHtml(model.path)}">${escapeHtml(folder)}</span>`;
  }

  function modelCatalogRoleTag(model) {
    if (model?.server_id && String(model?.source || '') === 'dflash-profile') {
      return '<span class="lm-badge blue" title="Registered DFlash engine profile">Engine profile</span>';
    }
    if (model?.library_file) {
      return '<span class="lm-badge gray" title="Same filename on disk, tied to a separate engine profile">Extra copy</span>';
    }
    return '';
  }

  function modelPathHint(model) {
    if (!model.path) return '';
    const showPath = model.duplicate_group
      || model.library_file
      || (model.dflash_stack && model.filename && model.label !== model.filename)
      || (model.stack_status === 'unregistered');
    if (!showPath) return '';
    return `<div class="lm-model-path-hint" title="${escapeHtml(model.path)}">${escapeHtml(shortPath(model.path))}</div>`;
  }

  function isDflashAccelerator(model) {
    if (!model) return false;
    if (model.accelerator_only === true) return true;
    if (model.accelerator_only === false) return false;
    if (typeof window.DFlashModelCard?.isAccelerator === 'function') {
      return window.DFlashModelCard.isAccelerator(model);
    }
    if (window.DFlashModelGroups?.isAcceleratorOnlyModel) {
      return window.DFlashModelGroups.isAcceleratorOnlyModel(model);
    }
    if (model.loadable && isDflashModel(model)) return false;
    const caps = Array.isArray(model.capabilities) ? model.capabilities : [];
    // A real DFlash stack target (dflash_stack, a draft_path, or the 'dflash'
    // capability) is NOT an accelerator. Only bare draft files (e.g.
    // gemma-4-12B-it-DFlash-Q4_K_M.gguf) are stack-only, even when their label
    // contains 'DFlash' (e.g. the unregistered "Gemma 12B DFlash" QAT target).
    if (model.dflash_stack || model.draft_path || caps.includes('dflash')) return false;
    const size = Number(model.size_gb);
    if (Number.isFinite(size) && size > 8) return false;
    const name = `${model.filename || ''} ${model.path || ''}`.toLowerCase();
    if (!name || name.startsWith('mmproj')) return false;
    return /\.gguf/i.test(name) && /dflash|dspark/.test(name);
  }

  function splitShardMeta(model) {
    const name = String(model?.filename || model?.path || '').split(/[/\\]/).pop() || '';
    const match = name.match(/-(\d{5})-of-(\d{5})\.gguf$/i);
    if (!match) return null;
    return { part: parseInt(match[1], 10), total: parseInt(match[2], 10) };
  }

  function importPathsForModel(model) {
    const splitFiles = Array.isArray(model?.split_files) ? model.split_files.filter(Boolean) : [];
    if (splitFiles.length > 1) return splitFiles.map(String);
    const path = String(model?.path || '').trim();
    return path ? [path] : [];
  }

  function importMenuLabels(model) {
    const paths = importPathsForModel(model);
    if (paths.length > 1) {
      return {
        copy: `Import all ${paths.length} shards to Flash Console`,
        move: `Move all ${paths.length} shards to Flash Console`,
      };
    }
    return { copy: 'Import to Flash Console', move: 'Move to Flash Console' };
  }

  // External models that are not already inside the Console's own library can
  // be copied/moved into the Console folder so they register under DFlash
  // Console and are managed by the app: GGUF files (llama-server) and
  // faster-whisper model directories (STT runtime).
  function canImportToConsole(model) {
    if (!model?.path || modelFileMissing(model)) return false;
    if (!isExternalModel(model)) return false;
    if (isDflashAccelerator(model)) return false;
    if (model.loadable && model.server_id) return false;
    if (importPathsForModel(model).length > 0) return true;
    if (model.kind === 'dir' || model.runtime_id === 'faster-whisper') return true;
    return false;
  }

  function draftHint(model) {
    if (!model.draft_filename && !model.draft_path) return '';
    const name = model.draft_filename || model.draft_path.split(/[/\\]/).pop();
    const size = model.draft_size_gb != null ? ` · ${model.draft_size_gb} GB` : '';
    const quant = model.draft_quant && model.draft_quant !== '—' ? ` ${model.draft_quant}` : '';
    return `<div class="lm-model-draft-hint">draft ${escapeHtml(name)}${escapeHtml(quant)}${escapeHtml(size)}</div>`;
  }

  // True when the Console's own library already contains a model with the same
  // file name as this external path — i.e. it was already imported once, so the
  // UI should not offer to import it again (shows "In Console library" instead).
  // Handles plain paths AND Hugging Face hub-cache paths, where the model name
  // lives in a "models--<org>--<name>" folder (e.g. OneVoice/STT uses those).
  function isModelAlreadyImported(externalPath) {
    const segments = String(externalPath || '').split(/[/\\]/).map((s) => s.trim().toLowerCase()).filter(Boolean);
    if (!segments.length) return false;
    const candidates = new Set();
    for (const seg of segments) {
      candidates.add(seg);
      // models--Systran--faster-whisper-small.en → faster-whisper-small.en
      const hub = seg.match(/^models--.+?--(.+)$/);
      if (hub) candidates.add(hub[1]);
    }
    const libraryNames = (models || [])
      .filter((m) => {
        const source = m.source || '';
        return source === 'dflash' || source === 'dflash-profile' || source === 'dflash-stack';
      })
      .map((m) => String(m.path || '').split(/[/\\]/).pop()?.trim().toLowerCase())
      .filter(Boolean);
    if (!libraryNames.length) return false;
    return [...candidates].some((c) => libraryNames.includes(c));
  }

  // A Console model is one the Console itself manages (its own library,
  // profiles or stacks). Everything else was discovered on disk or in another
  // app and is treated as external.
  function isConsoleModel(model) {
    const source = String(model?.source || '').trim().toLowerCase();
    if (source === 'dflash' || source === 'dflash-profile' || source === 'dflash-stack') return true;
    if (model?.library_file) return true;
    if (source === 'library') {
      const origin = String(model?.discovered_from || externalAppLabel(model) || '').trim();
      return !origin || origin === 'External';
    }
    return false;
  }

  function loadEngineFilterApplies() {
    return ['gguf', 'vllm', 'transformers', 'freetoken'].includes(modelTypeFilter);
  }

  function matchesLoadEngine(model, engine = getLoadEngine()) {
    const runtimeId = String(model?.runtime_id || '');
    const engines = Array.isArray(model?.engines) ? model.engines : [];
    const path = String(model?.path || '');
    const isGgufFile = /\.gguf$/i.test(path);

    if (engine === 'vllm') {
      return engines.includes('vllm') || runtimeId === 'vllm';
    }
    if (engine === 'transformers') {
      return engines.includes('transformers') || runtimeId === 'transformers';
    }
    if (engine === 'freetoken') {
      return engines.includes('freetoken') || runtimeId === 'freetoken';
    }

    // DFlash / GGUF — hide HF safetensors dirs and non-GGUF adapter models.
    if (isHfEngineModel(model) && String(model?.kind || '') === 'dir' && !isGgufFile) {
      return false;
    }
    if (engines.includes('vllm') || engines.includes('transformers') || engines.includes('freetoken')) {
      if (!isGgufFile && String(model?.kind || '') === 'dir') return false;
    }
    const nonGgufRuntimes = new Set(['faster-whisper', 'stt', 'piper', 'vibevoice', 'transformers', 'vllm', 'freetoken']);
    if (!isGgufFile && nonGgufRuntimes.has(runtimeId)) return false;
    if (String(model?.kind || '') === 'dir' && !model?.server_id && !model?.dflash_stack && !isGgufFile) {
      if (runtimeId === 'faster-whisper') return false;
      if (engines.includes('vllm') || engines.includes('transformers') || engines.includes('freetoken')) return false;
    }
    return true;
  }

  function loadEngineCounts() {
    const counts = { dflash: 0, vllm: 0, transformers: 0, freetoken: 0 };
    for (const model of models) {
      for (const engine of Object.keys(counts)) {
        if (matchesLoadEngine(model, engine)) counts[engine] += 1;
      }
    }
    return counts;
  }

  function bestLoadEngineForLibrary() {
    const counts = loadEngineCounts();
    let bestEngine = 'dflash';
    let bestCount = -1;
    for (const engine of LOAD_ENGINE_PREFERENCE) {
      const count = counts[engine] || 0;
      if (count > bestCount) {
        bestCount = count;
        bestEngine = engine;
      }
    }
    return [bestEngine, bestCount];
  }

  function autoPickLoadEngine() {
    // Follow the models that are actually on this PC. Prefer Transformers over
    // vLLM when both fit the same Hugging Face folders.
    if (!models.length || !loadEngineFilterApplies()) return;
    const counts = loadEngineCounts();
    const current = getLoadEngine();
    if (
      current === 'vllm'
      && counts.transformers >= counts.vllm
      && counts.transformers > 0
    ) {
      autoPickedLoadEngine = 'transformers';
      localStorage.setItem(LOAD_ENGINE_KEY, 'transformers');
      syncLoadEnginePicks();
      return;
    }
    if (counts[current] > 0) return;
    const [bestEngine, bestCount] = bestLoadEngineForLibrary();
    if (!bestCount || bestEngine === current) return;
    autoPickedLoadEngine = bestEngine;
    localStorage.setItem(LOAD_ENGINE_KEY, bestEngine);
    syncLoadEnginePicks();
  }

  function compareModelLabels(a, b) {
    return String(a?.label || a?.filename || '').localeCompare(
      String(b?.label || b?.filename || ''),
      undefined,
      { sensitivity: 'base', numeric: true },
    );
  }

  function isExternalModel(model) {
    return !isConsoleModel(model);
  }

  // Best-effort name of the external app/folder a model was discovered in.
  function externalAppLabel(model) {
    const discovered = String(model?.discovered_from || '').trim();
    if (discovered && discovered !== 'External') return discovered;
    const libLabel = String(model?.library_label || '').trim();
    if (libLabel) return libLabel;
    const path = String(model?.path || '').replace(/\\/g, '/').toLowerCase();
    if (path.includes('onevoice')) return 'OneVoice';
    if (path.includes('.lmstudio') || path.includes('/lm studio/')) return 'LM Studio';
    if (path.includes('huggingface')) return 'Hugging Face hub';
    if (path.includes('/ollama/') || path.includes('\\ollama\\')) return 'Ollama';
    return '';
  }

  // Ambiguous external sources (scanned folders) get the explicit "External"
  // tag; LM Studio / Ollama already have clear source labels.
  function needsExternalTag(model) {
    if (isConsoleModel(model)) return false;
    const source = String(model?.source || '').trim().toLowerCase();
    return source === 'local' || source === 'other' || source === 'unknown' || !source;
  }

  // Collapse duplicate on-disk copies of the same model in the library table:
  // when the Console has its own copy, keep only that one plus any currently
  // loaded external instance, and hide the other external duplicates (they
  // remain visible as engine cards and are importable from there). Groups with
  // no Console copy are kept whole so external models stay importable.
  function dedupeExternalCopies(rows) {
    const groups = new Map();
    for (const model of rows) {
      const file = String(model?.filename || model?.label || '').trim().toLowerCase();
      if (!file) continue;
      if (!groups.has(file)) groups.set(file, []);
      groups.get(file).push(model);
    }
    const keep = new Set();
    for (const group of groups.values()) {
      if (group.length < 2) {
        group.forEach((m) => keep.add(m));
        continue;
      }
      const hasConsole = group.some((m) => isConsoleModel(m));
      if (!hasConsole) {
        group.forEach((m) => keep.add(m));
        continue;
      }
      for (const m of group) {
        if (isConsoleModel(m) || isStackLoadedOnGpu(m)) keep.add(m);
      }
    }
    return rows.filter((m) => keep.has(m));
  }

  let serverPortById = {};

  function mergeModelsWithState(catalogModels, serversData, browsePrefs) {
    const serverMap = {};
    serverPortById = {};
    externalGpuLoads = Array.isArray(serversData?.external_gpu_loads)
      ? serversData.external_gpu_loads
      : [];
    const markers = collectLoadedMarkers(serversData);
    loadedServerIds = markers.serverIds;
    loadedPathKeys = markers.pathKeys;
    loadedModelIds = markers.modelIds;
    bootingServers = {};
    for (const server of serversData.servers || []) {
      serverMap[server.id] = server;
      const portNum = Number(server.port);
      if (Number.isFinite(portNum) && portNum > 0) serverPortById[server.id] = portNum;
      if (server.status === 'booting') {
        bootingServers[server.id] = {
          progress: server.load_progress?.expert_pct ?? server.load_progress ?? null,
          label: server.label || server.id,
          detail: server.load_progress?.detail || 'Loading model onto GPU…',
          path: server.model_path || server.target_path || '',
        };
      }
      if (server.id === 'freetoken' && (server.warming || server.booting || server.status === 'booting')) {
        const progress = server.load_progress || {};
        bootingServers.freetoken = {
          progress: progress.expert_pct ?? null,
          label: server.model_path ? String(server.model_path).split(/[\\/]/).pop() : 'FreeToken',
          detail: progress.detail || 'Warming up FreeToken experts…',
          path: server.model_path || '',
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
        const boundId = String(merged.bound_profile_id || '').trim();
        if (boundId && serverMap[boundId]) {
          const boundPort = Number(serverMap[boundId].port);
          if (Number.isFinite(boundPort) && boundPort > 0) merged.port = boundPort;
        }
        merged.runtime_loaded = isStackLoadedOnGpu(merged);
      }
      return merged;
    });
  }

  function modelIdentifier(model) {
    return model.model_id || model.id || model.server_id || model.filename || model.path || '';
  }

  function copyableModelName(model) {
    const synthetic = /^(stack-capable|library-file|ollama):/i;
    const label = String(model?.label || '').trim();
    const filename = String(model?.filename || '').trim();
    const publisher = String(model?.publisher || '').trim();
    const hfRepo = String(model?.hf_repo || model?.repo_id || '').trim();
    if (hfRepo && !synthetic.test(hfRepo)) return hfRepo;
    if ((model?.arch === 'hf' || model?.kind === 'dir') && publisher && label && !label.includes('/')) {
      return `${publisher}/${label}`;
    }
    if (window.DFlashModelGroups?.stackDisplayName && model?.dflash_stack && model?.draft_path) {
      const stackName = String(window.DFlashModelGroups.stackDisplayName(model) || '').trim();
      if (stackName) return stackName;
    }
    if (filename && /\.[a-z0-9]+$/i.test(filename) && !synthetic.test(filename)) return filename;
    if (label && !synthetic.test(label)) return label;
    const raw = String(model?.model_id || model?.id || '').trim();
    return raw.replace(synthetic, '') || filename || label || raw;
  }

  function stackTargetIssue(model) {
    if (!model?.path) {
      return {
        reason_code: 'no-path',
        reason: 'No local GGUF file is attached to this entry.',
      };
    }
    if (isNonDflashStackTarget(model)) {
      return {
        reason_code: 'not-stack-target',
        reason: 'Translation and companion models are not used as DFlash stack targets.',
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
    const shard = splitShardMeta(model);
    if (shard && shard.part !== 1) {
      return {
        reason_code: 'split-shard',
        reason: `This is shard ${shard.part} of ${shard.total}. Use shard 00001-of-${String(shard.total).padStart(5, '0')} for DFlash stacks, or import all shards into Flash Console first.`,
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
      && !isNonDflashStackTarget(model)
      && isHfAcceleratorAvailable(model)
      && ['no-accelerator', 'weak-match'].includes(result?.reason_code)
    );
  }

  function canReplaceStackDraft(model) {
    return !!(isDflashStack(model) && model.server_id && model.draft_path && model.path);
  }

  function stackMenuActionHtml(model) {
    const state = stackMenuState(model);
    const result = state.result || {};
    if (result.reason_code === 'already-stack' && canReplaceStackDraft(model)) {
      return `
        <button type="button" data-cmd="replace-draft" title="Compare local and Hugging Face accelerators and update this stack">
          Check for better accelerator…
        </button>
        <div class="df-stack-preflight df-model-stack-preflight is-unavailable">${escapeHtml(result.reason || 'This model is already registered as a DFlash stack.')}</div>`;
    }
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
      .filter((job) => job.status === 'downloading' || job.status === 'incomplete');
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
    const isStack = !!(model?.dflash_stack && model?.draft_path);
    const name = isStack && window.DFlashModelGroups?.stackDisplayName
      ? window.DFlashModelGroups.stackDisplayName(model)
      : (model.label || model.id || '—');
    const parts = [name];
    const nameHasQuant = /\b(?:Q\d|IQ\d|F16|F32|BF16)\b/i.test(name);
    if (!isStack && model.quant && model.quant !== '—' && !nameHasQuant) parts.push(model.quant);
    const sizeLabel = formatModelDiskSize(model);
    if (sizeLabel !== '—') parts.push(sizeLabel);
    return parts.join(' · ');
  }

  function modelFileMissing(model) {
    return !!(model?.path_missing || (model?.path && !model.path));
  }

  function installedDflashLogo(model) {
    if (!model?.path || model?.path_missing || !isDflashModel(model)) return '';
    return dflashLogoLabel('DFlash 1');
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
    const speed = window.DFlashDownloadQueue?.formatSpeed?.(job.speed_bps) || '';
    const eta = window.DFlashDownloadQueue?.formatEta?.(job.eta_seconds) || '';
    const repo = job.repo_id || 'Hugging Face';
    const filename = job.filename || '—';
    const title = downloadJobTitle(job);
    const incomplete = job.status === 'incomplete';
    const shardLabel = incomplete && job.shard_total
      ? `${job.shard_present || 0}/${job.shard_total} shards`
      : '';
    const stats = incomplete
      ? [bytes !== '—' ? bytes : '', shardLabel, 'Needs resume'].filter(Boolean).join(' · ')
      : [bytes !== '—' ? bytes : '', speed, eta].filter(Boolean).join(' · ');
    const sizeCell = bytes !== '—' ? bytes : (speed || '—');
    const statusTag = incomplete
      ? '<span class="lm-tag yellow">incomplete</span>'
      : '<span class="lm-tag green">downloading</span>';
    const action = incomplete
      ? `<button class="lm-btn ghost tiny lm-action-btn" type="button" data-action="resume-download" data-download-job-id="${escapeHtml(job.id)}" title="Resume downloading remaining files">Resume</button>`
      : '<span class="lm-tag dim">in progress</span>';
    const bar = incomplete && job.bytes_total && job.bytes_read
      ? `<div class="lm-model-download-bar" aria-hidden="true"><div class="lm-model-download-fill" style="width:${Math.max(1, Math.min(99, Math.round((Number(job.bytes_read) / Number(job.bytes_total)) * 100)))}%"></div></div>`
      : (incomplete
        ? ''
        : `<div class="lm-model-download-bar" aria-hidden="true"><div class="lm-model-download-fill${fillClass}"${fillStyle}></div></div>`);
    return `
      <tr class="lm-model-row downloading-model${incomplete ? ' incomplete-download' : ''}" data-download-job-id="${escapeHtml(job.id)}">
        <td class="lm-col-model">
          <div class="lm-model-title-line">
            ${statusTag}
            <span class="lm-model-title-text">${escapeHtml(title)}</span>
            ${incomplete ? '' : `<span class="lm-model-download-pct">${escapeHtml(pctLabel)}</span>`}
          </div>
          ${bar}
          <div class="lm-model-download-stats">${escapeHtml(stats || (incomplete ? 'Incomplete download' : 'Starting…'))}</div>
          <div class="lm-model-meta-line lm-model-download-meta">${escapeHtml(repo)} · ${escapeHtml(filename)}</div>
        </td>
        <td class="lm-col-meta">—</td>
        <td class="lm-col-meta">—</td>
        <td class="lm-col-meta">${escapeHtml(repo.split('/')[0] || 'HF')}</td>
        <td class="lm-col-meta">${escapeHtml(sizeCell)}</td>
        <td class="lm-col-meta">${incomplete ? 'Paused' : 'Now'}</td>
        <td class="lm-col-action">${action}</td>
      </tr>`;
  }

  function filterDownloadJobs(jobs, needle) {
    const n = normalizeSearchText(needle);
    if (!n) return jobs;
    return jobs.filter((job) => {
      const hay = normalizeSearchText([
        downloadJobTitle(job),
        job.repo_id,
        job.filename,
        job.path,
      ].join(' '));
      return hay.includes(n);
    });
  }

  const TYPE_FILTER_KEY = 'dflashConsole.modelsTypeFilter.v2';
  let typeFilter = 'all';
  const MODEL_TYPE_FILTER_KEY = 'dflashConsole.modelsModelTypeFilter';
  const MODEL_TYPE_FILTERS = new Set([
    'all',
    'gguf',
    'vllm',
    'transformers',
    'freetoken',
    'llm',
    'ocr',
    'translation',
    'speech-to-text',
    'text-to-speech',
    'embedding',
    'vision',
    'projector',
    'other',
    'hf-accelerator',
  ]);
  let modelTypeFilter = 'all';

  function resetLibraryToolbarDefaults({ render = true } = {}) {
    typeFilter = 'all';
    modelTypeFilter = 'all';
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.modelsFilter === 'all');
    });
    const modelTypePick = document.getElementById('modelsTypeFilter');
    if (modelTypePick) modelTypePick.value = 'all';
    syncLoadEnginePicks();
    if (render) {
      renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
      renderFooter(meta);
    }
  }

  let pollTimer = null;
  let pollPaused = false;
  let lastRenderSignature = '';

  function modelType(model) {
    if (isProjectorModel(model)) return 'projector';
    if (window.DFlashModelCard?.modality) {
      return window.DFlashModelCard.modality(model);
    }
    const modality = String(model?.modality || '').trim().toLowerCase();
    if (modality === 'projector') return 'projector';
    if (MODALITY_BADGES[modality]) return modality;
    return 'llm';
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
    if (!target || isDflashAccelerator(target) || isNonDflashStackTarget(target)) return false;
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
    if (hfAcceleratorStatus !== 'ready' || isDflashAccelerator(model) || isNonDflashStackTarget(model)) return false;
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
    if (modelTypeFilter === 'gguf') return matchesLoadEngine(model, 'dflash');
    if (modelTypeFilter === 'vllm') return matchesLoadEngine(model, 'vllm');
    if (modelTypeFilter === 'transformers') return matchesLoadEngine(model, 'transformers');
    if (modelTypeFilter === 'freetoken') return matchesLoadEngine(model, 'freetoken');
    if (isProjectorModel(model)) return modelTypeFilter === 'projector';
    if (modelTypeFilter === 'projector') return false;
    return modelTypeFilter === 'all' || modelType(model) === modelTypeFilter;
  }

  function modelSearchHaystack(model) {
    return normalizeSearchText([
      model?.label,
      model?.id,
      model?.path,
      model?.publisher,
      model?.hf_repo,
      model?.arch,
      model?.quant,
      model?.draft_label,
      model?.draft_filename,
      model?.draft_path,
      model?.stack_status,
      model?.filename,
    ].join(' '));
  }

  function modelMatchesSearch(model, needle) {
    if (!needle) return true;
    return modelSearchHaystack(model).includes(needle);
  }

  function showingAcceleratorsOnly() {
    return typeFilter === 'accelerators';
  }

  function shouldHideAcceleratorRow(model) {
    return isDflashAccelerator(model) && !showingAcceleratorsOnly();
  }

  function modelsRenderSignature(filterText) {
    const needle = normalizeSearchText(filterText);
    const rows = models.filter((model) => {
      if (shouldHideAcceleratorRow(model)) return false;
      if (typeFilter === 'dflash' && !isDflashStack(model)) return false;
      if (typeFilter === 'accelerators' && !isDflashAccelerator(model)) return false;
      if (typeFilter === 'loaded' && !isStackLoadedOnGpu(model)) return false;
      if (modelFileMissing(model)) return false; // never show a card for a missing file
      if (model?.incomplete) return false;
      // Text search must still find HF SafeTensors folders even when the type
      // filter is DFlash/GGUF — otherwise installed FreeToken/vLLM models vanish.
      if (!matchesModelType(model) && !(needle && modelMatchesSearch(model, needle))) return false;
      return modelMatchesSearch(model, needle);
    }).map((model) => [
      modelKey(model),
      model.label,
      model.filename,
      model.path,
      model.draft_path,
      model.size_gb,
      model.draft_size_gb,
      model.dflash_generation_label,
      model.accelerator_only,
      model.runtime_status,
      model.runtime_loaded,
      model.stack_status,
      model.duplicate_group,
      model.match_score,
    ].join(':')).join('|');
    return `${typeFilter}:${modelTypeFilter}:${hfAcceleratorRevision}:${selectedKey}:${needle}:${rows}`;
  }

  // Provider source label for the Source column (LM Studio, OneVoice, HF hub, etc.).
  function modelSourceLabel(model) {
    const discovered = String(model?.discovered_from || '').trim();
    if (discovered) return discovered;
    const libLabel = String(model?.library_label || '').trim();
    if (libLabel) return libLabel;
    const source = String(model?.source || '').trim().toLowerCase();
    if (source === 'lmstudio') return 'LM Studio';
    if (source === 'dflash' || source === 'dflash-profile' || source === 'dflash-stack') return 'DFlash';
    if (source === 'ollama') return 'Ollama';
    const app = externalAppLabel(model);
    if (app) return app;
    return 'External';
  }

  // Source cell: origin label with HF publisher as a muted suffix when useful.
  function modelSourceCell(model) {
    const label = modelSourceLabel(model);
    const publisher = String(model?.publisher || '').trim();
    if (!publisher || publisher.toLowerCase() === label.toLowerCase()) {
      return escapeHtml(label);
    }
    return `${escapeHtml(label)}<span class="lm-col-sub"> · ${escapeHtml(publisher)}</span>`;
  }

  function loadingRowHtml(message = 'Loading your model library…', note = 'Scanning configured folders. Your models will appear here shortly.') {
    return `<tr class="lm-models-loading-row"><td colspan="7">
      <span class="lm-models-loading-spinner" aria-hidden="true"></span>
      <span>${escapeHtml(message)}</span>
      <small>${escapeHtml(note)}</small>
    </td></tr>`;
  }

  function renderTable(filterText, { force = false } = {}) {
    const body = document.getElementById('modelsTableBody');
    if (!body) return;
    const signature = modelsRenderSignature(filterText);
    if (!force && signature === lastRenderSignature) return;
    lastRenderSignature = signature;
    const needle = normalizeSearchText(filterText);
    const pinned = loadPinnedSet();
    const activeDownloads = filterDownloadJobs(getActiveDownloadJobs(), needle);
    const visibleDownloads = modelTypeFilter === 'hf-accelerator' ? [] : activeDownloads;
    const catalogRows = models.filter((model) => {
      if (shouldHideAcceleratorRow(model)) return false;
      if (typeFilter === 'dflash' && !isDflashStack(model)) return false;
      if (typeFilter === 'accelerators' && !isDflashAccelerator(model)) return false;
      if (typeFilter === 'loaded' && !isStackLoadedOnGpu(model)) return false;
      if (modelFileMissing(model)) return false; // never show a card for a missing file
      // Incomplete shard folders belong in Downloading now with Resume.
      if (model?.incomplete) return false;
      // Text search must still find HF SafeTensors folders even when the type
      // filter is DFlash/GGUF — otherwise installed FreeToken/vLLM models vanish.
      if (!matchesModelType(model) && !(needle && modelMatchesSearch(model, needle))) return false;
      return modelMatchesSearch(model, needle);
    }).sort((a, b) => {
      const aPin = pinned.has(modelKey(a)) ? 0 : 1;
      const bPin = pinned.has(modelKey(b)) ? 0 : 1;
      if (aPin !== bPin) return aPin - bPin;
      const aConsole = isConsoleModel(a) ? 0 : 1;
      const bConsole = isConsoleModel(b) ? 0 : 1;
      if (aConsole !== bConsole) return aConsole - bConsole;
      if (typeFilter === 'dflash') {
        const aStack = STACK_SORT[a.stack_status || (a.loadable ? 'ready' : 'unregistered')] ?? 3;
        const bStack = STACK_SORT[b.stack_status || (b.loadable ? 'ready' : 'unregistered')] ?? 3;
        if (aStack !== bStack) return aStack - bStack;
      }
      return compareModelLabels(a, b);
    });
    // Hide redundant external copies when the Console already has the model.
    const visibleRows = dedupeExternalCopies(catalogRows);
    // How many copies of each filename are actually shown — used so the
    // "same name ×N" badge matches the visible cards (not the on-disk total).
    const visibleNameCounts = new Map();
    for (const m of visibleRows) {
      const file = String(m?.filename || m?.label || '').trim().toLowerCase();
      if (!file) continue;
      visibleNameCounts.set(file, (visibleNameCounts.get(file) || 0) + 1);
    }

    if (!visibleRows.length && !(typeFilter === 'loaded' ? [] : visibleDownloads).length) {
      if (catalogLoading) {
        // Catalog is still being fetched — show progress, not an empty state.
        body.innerHTML = loadingRowHtml();
        return;
      }
      const emptyLabel = modelTypeFilter === 'hf-accelerator'
        ? hfAcceleratorStatus === 'loading'
          ? 'Checking Hugging Face for compatible DFlash accelerators…'
          : hfAcceleratorStatus === 'error'
            ? 'Hugging Face could not be checked. Try this filter again when online.'
            : 'No local target models have a matching accelerator listed on Hugging Face.'
        : modelTypeFilter === 'projector'
          ? 'No vision projectors found. Projectors are mmproj companion files next to multimodal GGUF models.'
        : modelTypeFilter === 'gguf'
          ? 'No GGUF or DFlash-stack models match this filter.'
        : modelTypeFilter === 'vllm'
          ? 'No Hugging Face folders loadable on vLLM match this filter.'
        : modelTypeFilter === 'transformers'
          ? 'No Hugging Face folders loadable on Transformers match this filter.'
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
      ...visibleRows.map((model) => {
      const key = modelKey(model);
      const selected = key === selectedKey;
      const pinnedClass = pinned.has(key) ? ' pinned' : '';
      const pinMark = pinned.has(key) ? '<span class="lm-model-pin" title="Pinned">📌</span>' : '';
      const loadBtn = stackActionButton(model);
      const actionStack = loadBtn.includes('lm-action-stack');
      const actionStackSetup = loadBtn.includes('is-setup-row');
      const size = formatModelDiskSize(model);
      const dupVisible = visibleNameCounts.get(String(model?.filename || model?.label || '').trim().toLowerCase()) || 0;
      return `
        <tr class="${modelRowClassName(model, { selected, pinned: pinned.has(key) })}${isExternalModel(model) ? ' external-model' : ''}" data-model-key="${escapeHtml(key)}" data-model-id="${escapeHtml(model.id || '')}" data-server-id="${escapeHtml(model.server_id || '')}">
          <td class="lm-col-model">
            ${loadedRibbon(model)}
            <div class="lm-model-title-line"><span class="lm-model-title-text" title="${escapeHtml(modelTitleLine(model))}">${escapeHtml(modelTitleLine(model))}</span>${pinMark}</div>
            <div class="lm-model-tags-line">${capTags(model, dupVisible)}</div>
            ${window.DFlashModelCard?.detailsHtml?.(model) || `${modelPathHint(model)}${draftHint(model)}`}
          </td>
          <td class="lm-col-meta">${escapeHtml(model.arch || '—')}</td>
          <td class="lm-col-meta">${escapeHtml(model.params || '—')}</td>
          <td class="lm-col-meta">${modelSourceCell(model)}</td>
          <td class="lm-col-meta">${escapeHtml(size)}</td>
          <td class="lm-col-meta">${escapeHtml(model.modified || '—')}</td>
          <td class="lm-col-action${actionStack ? ' has-action-stack' : ''}${actionStackSetup ? ' has-action-stack-setup' : ''}">${loadBtn}</td>
        </tr>`;
    }),
    ].join('');

    const actionHeader = document.querySelector('.lm-view[data-view="models"] .lm-models-table thead .lm-col-action');
    const anyActionStack = body.querySelector('.lm-col-action.has-action-stack');
    const anyActionStackSetup = body.querySelector('.lm-col-action.has-action-stack-setup');
    if (actionHeader) {
      actionHeader.classList.toggle('has-action-stack', Boolean(anyActionStack));
      actionHeader.classList.toggle('has-action-stack-setup', Boolean(anyActionStackSetup));
    }

    body.querySelectorAll('.lm-model-row:not(.downloading-model)').forEach((row) => {
      row.addEventListener('click', (event) => {
        if (event.target.closest('[data-action="load-model"]')) return;
        if (event.target.closest('[data-action="load-llm"]')) return;
        if (event.target.closest('[data-action="open-folder"]')) return;
        if (event.target.closest('[data-action="browse-model"]')) return;
        if (event.target.closest('[data-action="enable-stack"]')) return;
        if (event.target.closest('[data-action="setup-stack"]')) return;
        if (event.target.closest('[data-action="unload-model"]')) return;
        if (event.target.closest('[data-engine-pick]')) return;
        void selectModel(row.dataset.modelKey);
      });
      row.addEventListener('dblclick', () => {
        const model = modelForRow(row);
        if (model?.loadable && !isDflashAccelerator(model)) void loadModel(model);
      });
      row.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        const model = modelForRow(row);
        if (model) openContextMenu(event, model);
      });
    });
    body.querySelectorAll('[data-action="load-model"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const model = modelForRow(btn.closest('.lm-model-row'));
        if (model) void loadModel(model);
      });
    });
    body.querySelectorAll('[data-action="resume-download"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const jobId = btn.dataset.downloadJobId || btn.closest('[data-download-job-id]')?.dataset.downloadJobId;
        if (jobId) void window.DFlashDownloadQueue?.resumeDownloadJob?.(jobId);
      });
    });
    body.querySelectorAll('[data-action="load-llm"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const model = modelForRow(btn.closest('.lm-model-row'));
        if (model) void loadModel(model, { llmOnly: true });
      });
    });
    body.querySelectorAll('[data-action="open-folder"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const model = modelForRow(btn.closest('.lm-model-row'));
        if (model) void browseModel(model);
      });
    });
    body.querySelectorAll('[data-action="browse-model"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const model = modelForRow(btn.closest('.lm-model-row'));
        if (model) void browseModel(model);
      });
    });
    body.querySelectorAll('[data-action="enable-stack"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const model = modelForRow(btn.closest('.lm-model-row'));
        if (model) void enableStack(model);
      });
    });
    body.querySelectorAll('[data-action="setup-stack"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const model = modelForRow(btn.closest('.lm-model-row'));
        if (model) void setupStack(model);
      });
    });
    body.querySelectorAll('[data-action="unload-model"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const model = modelForRow(btn.closest('.lm-model-row'));
        if (model) void unloadModel(model);
      });
    });
  }

  async function unloadModel(model) {
    const runtimeId = String(model?.runtime_id || '');
    const runtimeUnloadIds = new Set(['stt', 'faster-whisper', 'piper', 'transformers', 'vibevoice', 'vllm', 'freetoken']);
    const serverId = model?.server_id || '';
    markModelUnloadPending(model, serverId);
    window.DFlashStatusFeed?.setTransient(`Unloading ${model.label || model.id || serverId}…`, {
      secondary: 'Releasing GPU memory',
      ttlMs: 120000,
    });
    try {
      const pick = document.querySelector(`[data-engine-pick="${CSS.escape(modelKey(model))}"]`);
      const unloadRuntime = String(pick?.value || runtimeId || '');
      if ((runtimeUnloadIds.has(unloadRuntime) || runtimeUnloadIds.has(runtimeId)) && !model?.server_id) {
        const target = runtimeUnloadIds.has(unloadRuntime) ? unloadRuntime : runtimeId;
        await api(`/api/runtimes/${encodeURIComponent(target)}/unload`, { method: 'POST', timeoutMs: 0 });
        toast(`${model.label || model.id} unloaded`);
        await refreshRuntimeState({ silent: true });
        void refreshCatalogQuiet();
        if (window.DFlashServerLive?.refresh) {
          void window.DFlashServerLive.refresh(true, { fresh: true, includeExternal: true }).catch(() => {});
        }
        return;
      }
      if (!model?.server_id) {
        const external = findExternalGpuLoad(model);
        if (external?.pid) {
          const ok = await unloadExternalModel({
            pid: external.pid,
            api_url: external.api_url || '',
            model_id: external.model_id || external.model_name || external.title || '',
          });
          if (ok) {
            toast(`${model.label || model.id} unloaded`);
          } else {
            toast('Could not unload the external model', false);
          }
          await refreshRuntimeState({ silent: true });
          void refreshCatalogQuiet();
          if (window.DFlashServerLive?.refresh) {
            void window.DFlashServerLive.refresh(true, { fresh: true, includeExternal: true }).catch(() => {});
          }
          return;
        }
        toast('No engine profile for this model', false);
        return;
      }
      await api(`/api/servers/${encodeURIComponent(model.server_id)}/unload`, { method: 'POST', timeoutMs: 0 });
      toast(`${model.label || model.server_id} unloaded`);
      await refreshRuntimeState({ silent: true });
      void refreshCatalogQuiet();
      if (window.DFlashServerLive?.refresh) {
        void window.DFlashServerLive.refresh(true, { fresh: true, includeExternal: true }).catch(() => {});
      }
    } catch (err) {
      toast(err.message, false);
    } finally {
      clearModelUnloadPending(model, serverId);
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

  async function setupStack(model) {
    const pres = window.DFlashModelCard?.presentation?.(model) || {};
    const targetPath = String(model?.path || pres.targetPath || '').trim();
    const draftPath = String(model?.draft_path || pres.acceleratorPath || '').trim();
    if (!targetPath || !draftPath) {
      toast('Missing target or accelerator path', false);
      return;
    }
    try {
      await window.DFlashStackWizard?.open?.({
        targetPath,
        targetLabel: model.label || model.filename,
        draftPath,
        draftLabel: model.draft_filename || model.draft_label || pres.acceleratorName,
        pairedSetup: true,
      });
    } catch (err) {
      toast(err?.message || 'Could not open stack wizard', false);
    }
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

  // Dark in-app confirmation dialog (never the native Windows confirm box).
  function openConfirmDialog({ title, message, sub = '', confirmLabel = 'Delete', kicker = '', cancelLabel = 'Cancel' }) {
    const modal = document.getElementById('deleteModelConfirmModal');
    if (!modal) return Promise.resolve(false);
    const titleEl = document.getElementById('deleteModelConfirmTitle');
    const messageEl = document.getElementById('deleteModelConfirmMessage');
    const subEl = document.getElementById('deleteModelConfirmSub');
    const confirmBtn = document.getElementById('deleteModelConfirm');
    const cancelBtn = document.getElementById('deleteModelCancel');
    const closeBtn = document.getElementById('deleteModelConfirmClose')
      || modal.querySelector('[data-action="close-modal"]');
    const kickerEl = modal.querySelector('.df-update-kicker');
    if (titleEl) titleEl.textContent = title || 'Delete model?';
    if (messageEl) messageEl.textContent = message || 'Are you sure you want to delete this model? This action cannot be undone.';
    if (subEl) subEl.textContent = sub || '';
    if (confirmBtn) confirmBtn.textContent = confirmLabel || 'Delete';
    if (cancelBtn) cancelBtn.textContent = cancelLabel || 'Cancel';
    if (kickerEl) kickerEl.textContent = kicker || 'Delete model';

    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    window.setTimeout(() => confirmBtn?.focus(), 30);

    return new Promise((resolve) => {
      const backdrop = modal.querySelector('.lm-modal-backdrop');
      let settled = false;
      const cleanup = (result) => {
        if (settled) return;
        settled = true;
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
        if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
        cancelBtn?.removeEventListener('click', onCancel);
        confirmBtn?.removeEventListener('click', onConfirm);
        closeBtn?.removeEventListener('click', onClose);
        backdrop?.removeEventListener('click', onBackdrop);
        resolve(result);
      };
      const onCancel = () => cleanup(false);
      const onConfirm = () => cleanup(true);
      const onClose = () => cleanup(false);
      const onBackdrop = (e) => {
        if (e.target === backdrop) cleanup(false);
      };
      cancelBtn?.addEventListener('click', onCancel);
      confirmBtn?.addEventListener('click', onConfirm);
      closeBtn?.addEventListener('click', onClose);
      // Ignore the leftover click that closed the context menu so the
      // confirm dialog is not dismissed in the same gesture.
      window.setTimeout(() => {
        if (!settled) backdrop?.addEventListener('click', onBackdrop);
      }, 350);
    });
  }

  function rememberDeletedModel(model, extraPaths = []) {
    const key = modelKey(model);
    if (key) suppressedLibrary.keys.add(key);
    const repo = String(model.hf_repo || '').trim().toLowerCase();
    if (repo) suppressedLibrary.keys.add(`hf:${repo}`);
    [model.path, model.draft_path, ...extraPaths].filter(Boolean).forEach((p) => {
      const pathKey = normalizeModelPath(p);
      if (!pathKey) return;
      suppressedLibrary.paths.add(pathKey);
      const hub = pathKey.match(/^(.*models--[^/\\]+)/i);
      if (hub) suppressedLibrary.paths.add(hub[1]);
    });
  }

  function isSuppressedLibraryModel(model) {
    if (suppressedLibrary.keys.has(modelKey(model))) return true;
    const repo = String(model.hf_repo || '').trim().toLowerCase();
    if (repo && suppressedLibrary.keys.has(`hf:${repo}`)) return true;
    const pathKey = normalizeModelPath(model.path);
    if (!pathKey) return false;
    for (const banned of suppressedLibrary.paths) {
      if (pathKey === banned || pathKey.startsWith(`${banned}/`) || pathKey.startsWith(`${banned}\\`)) {
        return true;
      }
    }
    return false;
  }

  // Remove a just-deleted model from the local catalog so its card disappears
  // right away instead of lingering as "missing file" while the backend catalog
  // rescan catches up.
  function dropModelFromLibrary(model, extraPaths = []) {
    rememberDeletedModel(model, extraPaths);
    models = models.filter((m) => !isSuppressedLibraryModel(m));
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    renderFooter();
  }

  function openContextMenu(event, model) {
    const menu = document.getElementById('modelsContextMenu');
    if (!menu) return;
    contextModel = model;
    const key = modelKey(model);
    const pinned = loadPinnedSet();
    const isPinned = pinned.has(key);
    const hfUrl = huggingFaceUrl(model);
    const canDelete = !!model.path || !!model.server_id;

    const targetIssue = stackTargetIssue(model);
    const targetKey = modelKey(model);
    stackPreflight = targetIssue
      ? { key: targetKey, status: 'unavailable', result: targetIssue }
      : { key: targetKey, status: 'checking', result: null };

    const importLabels = importMenuLabels(model);

    menu.innerHTML = `
      <button type="button" data-cmd="pin">${isPinned ? 'Unpin' : 'Pin'}</button>
      <button type="button" data-cmd="copy-id">Copy identifier</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <button type="button" data-cmd="huggingface"${hfUrl ? '' : ' disabled'}>Open Hugging Face</button>
      <button type="button" data-cmd="add-vision"${canAddVision(model) ? '' : ' disabled'} title="Download vision projector from Hugging Face and wire it to this model">Add vision support…</button>
      ${canImportToConsole(model) ? `<button type="button" data-cmd="copy-to-console">${escapeHtml(importLabels.copy)}</button>
      <button type="button" data-cmd="move-to-console">${escapeHtml(importLabels.move)}</button>` : ''}
      <hr>
      <div id="modelsStackAction">${stackMenuActionHtml(model)}</div>
      ${isDflashAccelerator(model)
        ? '<div class="df-stack-preflight df-model-stack-preflight is-unavailable">Accelerators are loaded only with a full target model.</div>'
        : `<button type="button" data-cmd="load"${model.loadable ? '' : ' disabled'}>Load to Server</button>`}
      <button type="button" data-cmd="delete"${canDelete ? '' : ' disabled'}>Delete</button>`;

    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');
    // Keep the menu fully inside the viewport: flip it upward when the click
    // is near the bottom of the page, and shift left when it would overflow
    // the right edge, so the dropdown is never trimmed.
    const MARGIN = 8;
    const menuRect = menu.getBoundingClientRect();
    let menuLeft = event.clientX;
    let menuTop = event.clientY;
    if (menuLeft + menuRect.width + MARGIN > window.innerWidth) {
      menuLeft = Math.max(MARGIN, window.innerWidth - menuRect.width - MARGIN);
    }
    if (menuTop + menuRect.height + MARGIN > window.innerHeight) {
      menuTop = Math.max(MARGIN, event.clientY - menuRect.height - MARGIN);
    }
    menu.style.left = `${menuLeft}px`;
    menu.style.top = `${menuTop}px`;

    menu.querySelectorAll('button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const cmd = btn.dataset.cmd;
        const target = model;
        hideContextMenu();
        void runContextCommand(cmd, target);
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
      const id = copyableModelName(model);
      if (!id) return;
      await navigator.clipboard.writeText(id);
      toast('Model name copied');
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
    if (cmd === 'replace-draft') {
      if (!canReplaceStackDraft(model)) {
        toast('This stack cannot be updated from here', false);
        return;
      }
      window.DFlashStackWizard?.openReplaceDraft?.({
        serverId: model.server_id,
        targetPath: model.path,
        targetLabel: model.label || model.filename,
        currentDraftPath: model.draft_path,
        currentDraftLabel: model.draft_filename || model.draft_label,
        label: model.label,
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
      if (!canImportToConsole(model)) {
        toast('This model cannot be imported into the Console library', false);
        return;
      }
      // Always go through the import wizard so the user picks Copy vs Move
      // (with overwrite/abort handling) — the same flow as the external cards.
      const result = await importModelWithWizard({
        path: model.path,
        name: model.filename || model.label || '',
        defaultMode: cmd === 'move-to-console' ? 'move' : 'copy',
      });
      if (result && !result.canceled) {
        await refresh({ rebindInspector: true });
        if (window.DFlashServerLive?.refresh) await window.DFlashServerLive.refresh();
      }
      return;
    }
    if (cmd === 'delete') {
      if (!model.path && !model.server_id) {
        toast('Nothing to delete for this model', false);
        return;
      }
      const isOllama = model.source === 'ollama';
      const isRegisteredStack = isDflashStack(model) && model.loadable && model.server_id;
      const isHfFolder = model.kind === 'dir' || model.arch === 'hf';
      const name = model.filename || model.label || model.id || 'this model';
      await new Promise((resolve) => window.setTimeout(resolve, 50));
      const confirmed = await openConfirmDialog({
        title: `Delete ${name}?`,
        message: isOllama
          ? `Delete ${name} from Ollama? The manifest and model files will be removed. This cannot be undone.`
          : isRegisteredStack
            ? `This removes the "${name}" engine profile from Console and deletes its target and draft GGUF files from disk. This cannot be undone.`
            : isHfFolder
              ? `Delete ${name} from the library and remove its Hugging Face folder from disk? This cannot be undone.`
              : `Delete ${name} from the library and remove its files from disk? This cannot be undone.`,
        sub: isOllama
          ? (model.ollama_model || '')
          : (model.path || (model.draft_filename ? `draft: ${model.draft_filename}` : '')),
        confirmLabel: 'Delete model',
      });
      if (!confirmed) return;
      try {
        if (isStackLoadedOnGpu(model) || isStackBooting(model)) {
          await unloadModel(model);
        }
        const params = new URLSearchParams();
        if (model.path) params.set('path', model.path);
        const profileId = model.server_id || model.bound_profile_id;
        if (profileId) params.set('server_id', profileId);
        if (isOllama) {
          params.set('source', 'ollama');
          params.set('model_id', model.ollama_model || model.label || '');
        }
        const data = await api(`/api/models/file?${params.toString()}`, { method: 'DELETE', timeoutMs: 0 });
        const removedProfiles = Array.isArray(data?.removed_profiles) ? data.removed_profiles : [];
        const extraPaths = [
          ...(Array.isArray(data?.deleted_dirs) ? data.deleted_dirs : []),
          ...(Array.isArray(data?.deleted_files) ? data.deleted_files : []),
        ];
        if (selectedKey === key) selectedKey = '';
        dropModelFromLibrary(model, extraPaths);
        toast(data?.model
          ? `Deleted ${data.model}`
          : (removedProfiles.length ? `Deleted ${removedProfiles.join(', ')}` : `Deleted ${name}`));
        window.DFlashStatusFeed?.note('Model deleted', data?.model || name);
        await refresh({ rebindInspector: true, forceCatalogRefresh: true });
      } catch (err) {
        toast(err.message || 'Could not delete the model', false);
      }
    }
  }

  function renderFooter(data) {
    if (data) meta = data;
    const stats = document.getElementById('modelsFooterStats');
    const path = document.getElementById('modelsFooterPath');
    const hint = document.getElementById('modelsFooterHint');
    if (catalogLoading) {
      if (stats) stats.textContent = 'Loading models…';
      if (hint) hint.textContent = 'Scanning configured folders. Your models will appear here shortly.';
      return;
    }
    if (stats) {
      const activeCount = getActiveDownloadJobs().length;
      const hiddenAccelCount = showingAcceleratorsOnly() ? 0 : models.filter(isDflashAccelerator).length;
      const shown = typeFilter === 'dflash'
        ? models.filter(isDflashStack).length
        : typeFilter === 'accelerators'
          ? models.filter(isDflashAccelerator).length
          : typeFilter === 'loaded'
            ? models.filter(isStackLoadedOnGpu).length
            : models.length - hiddenAccelCount;
      const readyStacks = models.filter((model) => isDflashStack(model) && model.loadable).length;
      const filterNote = typeFilter === 'dflash'
        ? ` · showing ${shown} DFlash stacks (${readyStacks} ready to load)`
        : typeFilter === 'accelerators'
          ? ` · showing ${shown} accelerators`
          : typeFilter === 'loaded'
            ? ` · showing ${shown} loaded on GPU`
            : hiddenAccelCount
              ? ` · ${hiddenAccelCount} accelerators hidden`
              : '';
      const downloadNote = activeCount ? ` · ${activeCount} downloading` : '';
      stats.textContent = `${meta.total_count || models.length} models (${meta.loadable_count || 0} engine profiles), ${meta.total_size_gb || 0} GB total${filterNote}${downloadNote}`;
    }
    if (path) path.textContent = meta.models_dir || '—';
    if (hint) {
      const cacheNote = meta.cached
        ? (meta.stale ? 'Showing the last saved list while the library refreshes in the background. ' : 'Showing the saved list while checking for library changes. ')
        : '';
      hint.textContent = cacheNote + (typeFilter === 'loaded'
          ? 'Only models currently on the GPU (matched by engine profile or file path). Use Unload to free VRAM.'
          : typeFilter === 'accelerators'
          ? 'DFlash/DSpark draft files only (name contains DFlash or DSpark) — small checkpoints paired with a full target for speculative decoding. Full target GGUFs belong under All models.'
          : typeFilter === 'dflash'
            ? 'DFlash stacks with target + accelerator pairing. Gold label = speculative stack.'
            : modelTypeFilter === 'gguf'
              ? 'Showing GGUF files and DFlash stacks only. Use All model types to see everything.'
              : modelTypeFilter === 'vllm'
                ? 'Showing Hugging Face folders loadable on vLLM.'
                : modelTypeFilter === 'transformers'
                  ? 'Showing Hugging Face folders loadable on Transformers.'
                  : 'All discovered models from Console libraries and scanned folders on this PC. Accelerators are hidden unless you choose the Accelerators filter.');
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

  async function waitForFreeTokenReady(model, { timeoutMs = 900000 } = {}) {
    const label = model?.label || model?.id || 'model';
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const data = await api('/api/runtimes/freetoken');
      const progress = data?.load_progress || {};
      const pct = progress.expert_pct;
      const secondary = progress.detail
        || (pct != null ? `Warming up experts… ${pct}%` : 'Warming up FreeToken experts in WSL…');
      window.DFlashStatusFeed?.setTransient(`Loading ${label}…`, { secondary, ttlMs: timeoutMs });
      if (data?.inference_ready) {
        window.DFlashStatusFeed?.note(`${label} ready`, 'FreeToken is ready for chat');
        return data;
      }
      renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
      window.DFlashDownloadsLive?.render?.();
      await refreshRuntimeState({ silent: true });
      await new Promise((resolve) => window.setTimeout(resolve, 2500));
    }
    throw new Error('FreeToken did not finish warming up in time');
  }

  async function loadModel(model, { llmOnly = false } = {}) {
    if (isDflashAccelerator(model)) {
      toast('Choose the full target model; accelerators are stack-only.', false);
      return;
    }
    if (modelFileMissing(model)) {
      toast('Model file not found on disk. Refresh the catalog or check Settings → model folders.', false);
      return;
    }
    if (model.stack_status === 'unregistered' && !llmOnly) {
      toast('Set up a DFlash stack first, or use LLM on the row.', false);
      return;
    }
    const runtimeId = String(model.runtime_id || '');
    const adapterRuntimes = new Set(['stt', 'faster-whisper', 'piper', 'transformers', 'vibevoice', 'vllm', 'freetoken']);
    const pick = document.querySelector(`[data-engine-pick="${CSS.escape(modelKey(model))}"]`);
    const chosenRuntime = String(pick?.value || (isHfEngineModel(model) ? getLoadEngine() : '') || runtimeId || '');
    if (adapterRuntimes.has(chosenRuntime) || adapterRuntimes.has(runtimeId)) {
      if (!model?.path) {
        toast('This file is not available to load.', false);
        return;
      }
      const loadRuntime = adapterRuntimes.has(chosenRuntime) ? chosenRuntime : runtimeId;
      const isFw = loadRuntime === 'faster-whisper';
      const isTf = loadRuntime === 'transformers';
      const isVllm = loadRuntime === 'vllm';
      const isFreeToken = loadRuntime === 'freetoken';
      if ((isVllm || isTf || isFreeToken) && !isHfEngineModel(model)) {
        toast(
          isFreeToken
            ? 'FreeToken needs a Hugging Face SafeTensors folder and WSL2/NVIDIA support.'
            : (isVllm
              ? 'vLLM needs a Hugging Face SafeTensors folder. Pick DFlash / GGUF for GGUF files.'
              : 'Transformers needs a Hugging Face model folder. Pick DFlash / GGUF for GGUF files.'),
          false,
        );
        return;
      }
      if (window.DFlashComponentInstall?.isOnDemand?.(loadRuntime)) {
        const ready = await window.DFlashComponentInstall.ensure(loadRuntime, {
          modelLabel: model.label || model.filename || model.id || '',
        });
        if (!ready) {
          clearModelLoadPending(model, '');
          return;
        }
      }
      markModelLoadPending(model, isFreeToken ? 'freetoken' : '');
      window.DFlashStatusFeed?.setTransient(`Loading ${model.label || model.id}…`, {
        secondary: isFreeToken
          ? 'Starting FreeToken through WSL2 — expert banks warm up after the server port opens'
          : isVllm
          ? 'Starting vLLM — first load can take several minutes'
          : (isTf
            ? 'Loading Transformers model into GPU/CPU'
            : (isFw ? 'Loading faster-whisper model into GPU' : 'Loading speech model')),
        ttlMs: 900000,
      });
      try {
        const data = await api('/api/models/load', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            path: model.path || model.ollama_model || model.label || '',
            model_id: model.ollama_model || model.model_id || model.id || '',
            runtime_id: loadRuntime,
            load_settings: isVllm ? { preset: 'balanced' } : undefined,
          }),
          timeoutMs: 0,
        });
        if (isFreeToken && (data?.warming || !data?.loaded)) {
          await waitForFreeTokenReady(model);
          toast(`${model.label || model.id} loaded`);
        } else if (data?.loaded) {
          toast(`${model.label || model.id} loaded`);
          window.DFlashStatusFeed?.note(
            `${model.label || model.id} ready`,
            data.device ? `${data.device} · ${data.compute_type || ''}` : 'ready',
          );
        }
      } catch (err) {
        toast(err.message || `Could not load ${model.label || model.id}`, false);
      } finally {
        clearModelLoadPending(model, '');
      }
      await refreshRuntimeState({ silent: true });
      void refreshCatalogQuiet();
      if (window.DFlashServerLive?.refresh) {
        void window.DFlashServerLive.refresh(true, { fresh: true, includeExternal: true }).catch(() => {});
      }
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
    let serverId = llmOnly ? '' : model.server_id;
    if (model.plain_gguf || llmOnly || !serverId) {
      const active = window.DFlashServerLive.activeServer?.();
      serverId = active?.id;
      if (!serverId) {
        toast('Select an engine on the Engines tab first', false);
        return;
      }
    }
    markModelLoadPending(model, serverId);
    window.DFlashStatusFeed?.setTransient(`Loading ${model.label || model.id}…`, {
      secondary: model.server_id ? 'Reading weights into GPU' : 'Loading onto active engine',
      ttlMs: 120000,
    });
    try {
      const loaded = await window.DFlashServerLive.loadModelOnServer(serverId, model);
      if (loaded === false) return;
      await refreshRuntimeState({ silent: true });
      void refreshCatalogQuiet();
      if (window.DFlashServerLive?.refresh) {
        void window.DFlashServerLive.refresh(true, { fresh: true, includeExternal: true }).catch(() => {});
      }
    } catch (err) {
      toast(err.message || `Could not load ${model.label || model.id}`, false);
    } finally {
      clearModelLoadPending(model, serverId);
    }
  }

  async function fetchServersForLibrary() {
    try {
      const [serversData, tfRuntime, vllmRuntime] = await Promise.all([
        api('/api/servers?include_external=true', { timeoutMs: 20000 }),
        api('/api/runtimes/transformers', { timeoutMs: 8000 }).catch(() => null),
        api('/api/runtimes/vllm', { timeoutMs: 8000 }).catch(() => null),
      ]);
      if (tfRuntime?.active_model) {
        serversData.transformers_runtime = tfRuntime;
      }
      if (vllmRuntime?.active_model) {
        serversData.vllm_runtime = vllmRuntime;
      }
      return serversData;
    } catch (_err) {
      try {
        const [serversData, tfRuntime, vllmRuntime] = await Promise.all([
          api('/api/servers?include_external=false', { timeoutMs: 12000 }),
          api('/api/runtimes/transformers', { timeoutMs: 8000 }).catch(() => null),
          api('/api/runtimes/vllm', { timeoutMs: 8000 }).catch(() => null),
        ]);
        if (tfRuntime?.active_model) {
          serversData.transformers_runtime = tfRuntime;
        }
        if (vllmRuntime?.active_model) {
          serversData.vllm_runtime = vllmRuntime;
        }
        return serversData;
      } catch (_err2) {
        return { servers: [], external_gpu_loads: [] };
      }
    }
  }

  async function refreshRuntimeState({ silent = true } = {}) {
    const filter = document.getElementById('modelsFilterInput')?.value || '';
    try {
      const serversData = await fetchServersForLibrary();
      models = mergeModelsWithState(models, serversData, loadBrowsePrefs());
      renderFooter(meta);
      renderTable(filter, { force: true });
    } catch (err) {
      if (!silent) throw err;
    }
  }

  function stopRuntimePoll() {
    if (!runtimePollTimer) return;
    window.clearInterval(runtimePollTimer);
    runtimePollTimer = null;
  }

  function startRuntimePoll() {
    stopRuntimePoll();
    runtimePollTimer = window.setInterval(() => {
      if (document.body.dataset.activeView !== 'models' && document.body.dataset.activeView !== 'downloads') return;
      if (!pendingModelLoads.size && !pendingServerLoads.size
        && !pendingModelUnloads.size && !pendingServerUnloads.size
        && !bootingServers.freetoken) {
        stopRuntimePoll();
        return;
      }
      void refreshRuntimeState({ silent: true });
    }, 400);
  }

  function markModelLoadPending(model, serverId) {
    const key = modelKey(model);
    pendingModelLoads.set(key, {
      serverId: serverId || model.server_id || '',
      label: model.label || model.id || key,
    });
    if (serverId) pendingServerLoads.add(serverId);
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    startRuntimePoll();
  }

  function clearModelLoadPending(model, serverId) {
    pendingModelLoads.delete(modelKey(model));
    if (serverId) pendingServerLoads.delete(serverId);
    if (!pendingModelLoads.size && !pendingServerLoads.size
      && !pendingModelUnloads.size && !pendingServerUnloads.size) {
      stopRuntimePoll();
    }
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
  }

  function markModelUnloadPending(model, serverId) {
    const key = modelKey(model);
    pendingModelUnloads.set(key, {
      serverId: serverId || model.server_id || '',
      label: model.label || model.id || key,
    });
    if (serverId) pendingServerUnloads.add(serverId);
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    startRuntimePoll();
  }

  function clearModelUnloadPending(model, serverId) {
    pendingModelUnloads.delete(modelKey(model));
    if (serverId) pendingServerUnloads.delete(serverId);
    if (!pendingModelLoads.size && !pendingServerLoads.size
      && !pendingModelUnloads.size && !pendingServerUnloads.size) {
      stopRuntimePoll();
    }
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
  }

  async function refreshCatalogQuiet() {
    try {
      await refresh({ silent: true });
    } catch {
      /* keep the last saved list */
    }
  }

  async function performRefresh({ rebindInspector = false, forceCatalogRefresh = false, silent = false } = {}) {
    const filter = document.getElementById('modelsFilterInput')?.value || '';
    // Only the first load needs the loading state; polling refreshes keep the
    // current rows on screen (no flicker).
    const firstLoad = models.length === 0;
    if (firstLoad) {
      catalogLoading = true;
      renderTable(filter, { force: true });
      renderFooter();
    }
    const modelsPath = forceCatalogRefresh ? '/api/models?refresh=1' : '/api/models';
    const catalogTimeout = forceCatalogRefresh ? 0 : 12000;
    let data;
    let serversData;
    let catalogError = null;
    try {
      [data, serversData] = await Promise.all([
        api(modelsPath, { timeoutMs: catalogTimeout }),
        fetchServersForLibrary(),
      ]);
    } catch (err) {
      catalogError = err;
      try {
        serversData = await fetchServersForLibrary();
      } catch (_serverErr) {
        if (firstLoad) {
          catalogLoading = false;
          renderTable(filter, { force: true });
          renderFooter();
        }
        if (!silent) throw catalogError;
        return;
      }
      if (!models.length) {
        if (firstLoad) {
          catalogLoading = false;
          renderTable(filter, { force: true });
          renderFooter();
        }
        if (!silent) throw catalogError;
        return;
      }
      data = { models };
    }
    const partialCatalog = data?.partial === true;
    // A partial response contains only configured profiles while the disk
    // scan continues. Preserve the full visible list during that interval so
    // filters and selection do not flash or temporarily lose local models.
    const sourceModels = partialCatalog && models.length
      ? models
      : (data?.models || models);
    models = mergeModelsWithState(sourceModels, serversData, loadBrowsePrefs())
      .filter((row) => !isSuppressedLibraryModel(row));
    autoPickLoadEngine();
    if (data?.models && !partialCatalog) saveCachedModelCatalog(data);
    catalogLoading = false;
    if (partialCatalog) {
      if (partialRetryTimer) window.clearTimeout(partialRetryTimer);
      partialRetryTimer = window.setTimeout(() => {
        partialRetryTimer = null;
        void refreshCatalogQuiet();
      }, 900);
    } else if (partialRetryTimer) {
      window.clearTimeout(partialRetryTimer);
      partialRetryTimer = null;
    }
    meta = { ...(meta || {}), ...(data || {}), partial: partialCatalog };
    renderFooter(data);
    renderTable(filter, { force: firstLoad });
    if (!selectedKey || !models.some((m) => modelKey(m) === selectedKey)) {
      const firstConfigured = models.find((m) => m.loadable && !isDflashAccelerator(m));
      if (firstConfigured) await selectModel(modelKey(firstConfigured), { applyInspector: true });
      else if (models[0]) await selectModel(modelKey(models[0]), { applyInspector: true });
    } else if (rebindInspector) {
      await selectModel(selectedKey, { applyInspector: true });
    }
  }

  async function refresh(options = {}) {
    if (catalogRefreshInFlight) return catalogRefreshInFlight;
    catalogRefreshInFlight = performRefresh(options).finally(() => {
      catalogRefreshInFlight = null;
    });
    return catalogRefreshInFlight;
  }

  function normalizeTypeFilter(next) {
    return ['all', 'dflash', 'accelerators', 'loaded'].includes(next) ? next : 'all';
  }

  function normalizeModelTypeFilter(next) {
    if (!MODEL_TYPE_FILTERS.has(next)) return 'all';
    if (next === 'dflash' || next === 'accelerators') return 'all';
    return next;
  }

  function applyModelTypeFilter(next) {
    modelTypeFilter = normalizeModelTypeFilter(next);
    const modelTypePick = document.getElementById('modelsTypeFilter');
    if (modelTypePick && modelTypePick.value !== modelTypeFilter) {
      modelTypePick.value = modelTypeFilter;
    }
    if (modelTypeFilter === 'gguf') setLoadEngine('dflash');
    else if (modelTypeFilter === 'vllm' || modelTypeFilter === 'transformers' || modelTypeFilter === 'freetoken') {
      setLoadEngine(modelTypeFilter);
    }
    if (modelTypeFilter === 'hf-accelerator') void ensureHfAcceleratorCatalog();
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    renderFooter(meta);
  }

  function setTypeFilter(next) {
    const previous = typeFilter;
    typeFilter = normalizeTypeFilter(next);
    if (typeFilter === 'accelerators' && modelTypeFilter !== 'all') {
      modelTypeFilter = 'all';
      const modelTypePick = document.getElementById('modelsTypeFilter');
      if (modelTypePick) modelTypePick.value = 'all';
    }
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.modelsFilter === typeFilter);
    });
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    renderFooter(meta);
    if (typeFilter === 'all' && previous !== 'all') {
      void refresh({ forceCatalogRefresh: true }).catch(() => {});
    } else if (typeFilter === 'dflash' && previous !== 'dflash') {
      void refreshCatalogQuiet();
    } else if (typeFilter === 'loaded') {
      void refresh().catch(() => {});
    }
  }

  function onDownloadQueueUpdate() {
    if (document.body.dataset.activeView !== 'models') return;
    renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    renderFooter(meta);
  }

  function bind() {
    try {
      localStorage.removeItem(TYPE_FILTER_KEY);
      localStorage.removeItem(MODEL_TYPE_FILTER_KEY);
    } catch (_err) { /* ignore */ }
    resetLibraryToolbarDefaults({ render: false });
    document.getElementById('modelsFilterInput')?.addEventListener('input', (e) => {
      renderTable(e.target.value, { force: true });
    });
    const modelTypePick = document.getElementById('modelsTypeFilter');
    if (modelTypePick) {
      modelTypePick.addEventListener('change', (event) => {
        const next = normalizeModelTypeFilter(event.target.value);
        if (next === 'hf-accelerator' || ['gguf', 'vllm', 'transformers', 'freetoken'].includes(next)) {
          if (typeFilter !== 'all') {
            typeFilter = 'all';
            document.querySelectorAll('[data-models-filter]').forEach((btn) => {
              btn.classList.toggle('active', btn.dataset.modelsFilter === typeFilter);
            });
          }
        }
        applyModelTypeFilter(next);
      });
    }
    document.querySelectorAll('[data-load-engine-pick]').forEach((el) => {
      if (el.id === 'modelsEngineKindPick') return;
      el.addEventListener('change', () => {
        setLoadEngine(el.value);
        renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
      });
    });
    syncLoadEnginePicks();
    window.addEventListener('dflash-load-engine', () => {
      syncLoadEnginePicks();
      renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    });
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.addEventListener('click', () => setTypeFilter(btn.dataset.modelsFilter));
    });
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
        if (pendingModelLoads.size || pendingServerLoads.size
          || pendingModelUnloads.size || pendingServerUnloads.size) {
          void refreshRuntimeState({ silent: true });
          return;
        }
        void refresh({ silent: true }).catch(() => {});
      }
    }, MODEL_LIST_REFRESH_MS);
  }

  document.addEventListener('DOMContentLoaded', () => {
    const cached = loadCachedModelCatalog();
    if (cached) {
      models = cached.models;
      meta = cached.footer || {};
      catalogLoading = false;
    }
    bind();
    if (cached) {
      renderFooter(meta);
      renderTable(document.getElementById('modelsFilterInput')?.value || '', { force: true });
    }
    const tableWrap = document.querySelector('.lm-view[data-view="models"] .lm-models-table-wrap');
    tableWrap?.addEventListener('mouseenter', () => { pollPaused = true; });
    tableWrap?.addEventListener('mouseleave', () => { pollPaused = false; });
    void refresh({ rebindInspector: true })
      .then(() => {
        void refreshCatalogQuiet();
        startPolling();
      })
      .catch((err) => {
        if (!cached) toast(err.message, false);
        startPolling();
      });
  });

  // From an engine card: jump to the same model in the Model library and select
  // it so the user can Load / set up a stack / delete / etc. Returns true when
  // a matching library model was found.
  async function revealModelFromEngineCard({ path = '', serverId = '', modelId = '', label = '' } = {}) {
    if (!models.length) {
      try { await refresh(); } catch (_err) { /* catalog may be empty */ }
    }
    let found = findModelForDownload({ path, serverId, modelId, label });
    if (!found) {
      try { await refreshCatalogQuiet(); } catch (_err) { /* ignore */ }
      found = findModelForDownload({ path, serverId, modelId, label });
    }
    if (!found) return false;
    // Make sure the row is visible regardless of the current filters.
    if (typeFilter !== 'all') setTypeFilter('all');
    const input = document.getElementById('modelsFilterInput');
    if (input && input.value) {
      input.value = '';
      renderTable('', { force: true });
    }
    await selectModel(modelKey(found), { applyInspector: true });
    requestAnimationFrame(() => {
      const row = document.querySelector(`[data-model-key="${CSS.escape(modelKey(found))}"]`);
      row?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    });
    return true;
  }

  function findModelForDownload({ path = '', serverId = '', modelId = '', label = '' } = {}) {
    const norm = (v) => String(v || '').replace(/\\/g, '/').toLowerCase();
    const pathKey = norm(path);
    const serverKey = norm(serverId);
    const idKey = norm(modelId);
    const labelKey = norm(label);
    return models.find((m) => {
      if (pathKey && m.path) {
        const modelPath = norm(m.path);
        if (modelPath === pathKey) return true;
        if (pathKey.startsWith(`${modelPath}/`) || modelPath.startsWith(`${pathKey}/`)) return true;
      }
      if (serverKey && m.server_id && norm(m.server_id) === serverKey) return true;
      if (idKey && m.id && norm(m.id) === idKey) return true;
      if (idKey && m.model_id && norm(m.model_id) === idKey) return true;
      if (idKey && m.repo_id && norm(m.repo_id) === idKey) return true;
      if (labelKey && m.label && norm(m.label) === labelKey) return true;
      return false;
    }) || null;
  }

  function syntheticModelFromDownload(job, meta = {}) {
    const path = String(job?.path || '').trim();
    if (!path) return null;
    const repoId = String(job?.repo_id || '').trim();
    const filename = String(job?.filename || '').trim();
    const kind = (!filename || String(job?.kind || '').toLowerCase() === 'repo') ? 'dir' : 'file';
    const label = repoId.split('/').pop() || filename || path.split(/[\\/]/).pop() || 'Model';
    const totalBytes = Number(job?.bytes_total || job?.disk_bytes || 0);
    const model = {
      path,
      kind,
      modality: String(meta?.modality || 'llm'),
      label,
      repo_id: repoId,
      filename,
      loadable: true,
      id: repoId ? `hf:${repoId.toLowerCase()}` : `library-file:${normalizeModelPath(path)}`,
      size_gb: Number.isFinite(totalBytes) && totalBytes > 0 ? totalBytes / (1024 ** 3) : undefined,
    };
    if (kind === 'dir' && model.modality === 'llm') {
      model.engines = HF_LOAD_ENGINES.slice();
      model.runtime_id = 'transformers';
    }
    return model;
  }

  async function ensureModelForDownload(job, meta = {}) {
    if (!job) return null;
    const path = String(job.path || '').trim();
    const repoId = String(job.repo_id || '').trim();
    const label = String(job.filename || repoId || '').trim();
    let found = findModelForDownload({ path, modelId: repoId, label });
    if (!found && path) {
      try {
        await refreshCatalogQuiet();
      } catch {
        /* catalog may be empty */
      }
      found = findModelForDownload({ path, modelId: repoId, label });
    }
    if (found) return found;
    return syntheticModelFromDownload(job, meta);
  }

  // Wizard for importing an external model file into the Console library —
  // lets the user choose Copy (keep the original) or Move (remove it from its
  // current location). Resolves with 'copy' | 'move' | null (null = cancelled).
  function openImportToConsoleWizard({ path = '', name = '', defaultMode = 'copy' } = {}) {
    return new Promise((resolve) => {
      const modal = document.getElementById('importToConsoleModal');
      if (!modal) { resolve(defaultMode === 'move' ? 'move' : 'copy'); return; }
      const titleEl = document.getElementById('importToConsoleTitle');
      const pathEl = document.getElementById('importToConsolePath');
      const confirmBtn = document.getElementById('importToConsoleConfirm');
      const cancelBtn = document.getElementById('importToConsoleCancel');
      const backdrop = modal.querySelector('.lm-modal-backdrop');
      const closeBtn = modal.querySelector('[data-action="close-modal"]');
      const label = name || String(path || '').split(/[\\/]/).pop() || 'this model';
      // Respect the requested default mode (Move for "Move to Flash Console").
      modal.querySelectorAll('input[name="importMode"]').forEach((radio) => {
        radio.checked = radio.value === (defaultMode === 'move' ? 'move' : 'copy');
      });
      if (titleEl) titleEl.textContent = `Import ${label} to Flash Console?`;
      if (pathEl) pathEl.textContent = path || '';
      const cleanup = (result) => {
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
        if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
        cancelBtn?.removeEventListener('click', onCancel);
        confirmBtn?.removeEventListener('click', onConfirm);
        closeBtn?.removeEventListener('click', onCancel);
        backdrop?.removeEventListener('click', onBackdrop);
        resolve(result);
      };
      const currentMode = () => {
        const checked = modal.querySelector('input[name="importMode"]:checked');
        return checked?.value === 'move' ? 'move' : 'copy';
      };
      const onCancel = () => cleanup(null);
      const onConfirm = () => cleanup(currentMode());
      const onBackdrop = (e) => { if (e.target === backdrop) cleanup(null); };
      cancelBtn?.addEventListener('click', onCancel);
      confirmBtn?.addEventListener('click', onConfirm);
      closeBtn?.addEventListener('click', onCancel);
      backdrop?.addEventListener('click', onBackdrop);
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
    });
  }

  // Unload a model currently loaded in an external app (e.g. LM Studio) so the
  // file is released before a Copy/Move, and no stale external card lingers.
  async function unloadExternalModel({ pid, api_url = '', model_id = '' } = {}) {
    if (!pid) return false;
    try {
      await api(`/api/gpu/processes/${encodeURIComponent(pid)}/unload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_url, model_id }),
      });
      return true;
    } catch (_err) {
      // Non-fatal: continue even if the external unload failed.
      return false;
    }
  }

  // After importing a model, refresh BOTH the Model library and the Engines
  // dropdown catalog, then confirm the copy is actually registered in the
  // catalog (i.e. really present in the Flash Console folder + visible).
  function importedModelFilename(path) {
    return String(path || '').split(/[/\\]/).pop()?.trim().toLowerCase() || '';
  }

  function findImportedModelInLibrary(libraryPath, sourcePath = '') {
    const libraryKey = normalizeModelPath(libraryPath);
    const fileName = importedModelFilename(libraryPath) || importedModelFilename(sourcePath);
    if (!libraryKey && !fileName) return null;
    const exact = models.find((m) => libraryKey && normalizeModelPath(m.path) === libraryKey);
    if (exact) return exact;
    const consoleMatches = models.filter((m) => {
      if (!isConsoleModel(m)) return false;
      const rowFile = importedModelFilename(m.filename || m.path);
      return fileName && rowFile === fileName;
    });
    if (consoleMatches.length === 1) return consoleMatches[0];
    if (libraryKey) {
      return consoleMatches.find((m) => normalizeModelPath(m.path) === libraryKey) || null;
    }
    return consoleMatches[0] || null;
  }

  function importedModelVisible(libraryPath, sourcePath = '') {
    return !!findImportedModelInLibrary(libraryPath, sourcePath);
  }

  async function refreshImportedModelViews(libraryPath, sourcePath = '') {
    if (window.DFlashServerLive?.refreshCatalog) {
      await window.DFlashServerLive.refreshCatalog({ force: true, shouldRender: true });
    }
    if (!libraryPath) return false;
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await refresh({
        forceCatalogRefresh: attempt === 0 || attempt % 2 === 0,
        rebindInspector: attempt === 9,
      });
      const found = findImportedModelInLibrary(libraryPath, sourcePath);
      if (found) {
        await selectModel(modelKey(found), { applyInspector: true });
        const row = document.querySelector(`[data-model-key="${CSS.escape(modelKey(found))}"]`);
        row?.scrollIntoView?.({ block: 'nearest' });
        return true;
      }
      if (attempt < 9) {
        await new Promise((resolve) => window.setTimeout(resolve, 400));
      }
    }
    return false;
  }

  // Progress overlay while Copy/Move runs — polls backend byte progress during
  // the import API call and shows phase labels for unload/refresh steps.
  function newImportProgressId() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return `import-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function formatImportBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = bytes;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    return `${size >= 100 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
  }

  function importProgressPhaseText(phase, mode, row = {}) {
    const pct = Number(row.progress);
    const hasPct = Number.isFinite(pct) && pct >= 0;
    const suffix = hasPct ? ` (${Math.round(pct)}%)` : '';
    const done = formatImportBytes(row.bytes_done);
    const total = formatImportBytes(row.bytes_total);
    const sizeHint = done && total ? ` · ${done} / ${total}` : (done ? ` · ${done}` : '');
    if (phase === 'unloading') return 'Releasing model in the external app…';
    if (phase === 'moving') return `Moving into Flash Console${suffix}${sizeHint}`;
    if (phase === 'copying') return `Copying into Flash Console${suffix}${sizeHint}`;
    if (phase === 'refreshing') return 'Updating model library…';
    return mode === 'move' ? 'Moving into Flash Console…' : 'Copying into Flash Console…';
  }

  function setImportProgressUi({ title, text, hint, progress = null, indeterminate = false } = {}) {
    const modal = document.getElementById('importProgressModal');
    const titleEl = document.getElementById('importProgressTitle');
    const textEl = document.getElementById('importProgressText');
    const hintEl = document.getElementById('importProgressHint');
    const barEl = document.getElementById('importProgressBar');
    if (!modal) return;
    if (titleEl && title) titleEl.textContent = title;
    if (textEl && text) textEl.textContent = text;
    if (hintEl && hint != null) hintEl.textContent = hint;
    if (barEl) {
      barEl.classList.toggle('is-indeterminate', !!indeterminate);
      const pct = Number(progress);
      if (indeterminate || !Number.isFinite(pct)) {
        barEl.style.width = indeterminate ? '35%' : '8%';
      } else {
        barEl.style.width = `${Math.max(4, Math.min(100, pct))}%`;
      }
    }
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
  }

  function closeImportProgressUi() {
    const modal = document.getElementById('importProgressModal');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
  }

  function startImportProgressPolling(progressId, { mode = 'copy' } = {}) {
    let stopped = false;
    const poll = async () => {
      while (!stopped) {
        try {
          const data = await api(`/api/models/import-progress/${encodeURIComponent(progressId)}`, { timeoutMs: 8000 });
          if (data?.success) {
            const phase = data.phase || (mode === 'move' ? 'moving' : 'copying');
            const indeterminate = data.progress == null && phase !== 'copying' && phase !== 'moving';
            setImportProgressUi({
              text: importProgressPhaseText(phase, mode, data),
              progress: data.progress,
              indeterminate,
            });
          }
        } catch (_) {
          /* keep polling until the import POST finishes */
        }
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
    };
    void poll();
    return () => { stopped = true; };
  }

  // Open the import wizard, then: 1) unload the external model, 2) Copy/Move it
  // into the Console library (asking before overwriting an existing model), and
  // 3) refresh the Model library + Engines dropdown so it shows up.
  // Returns { canceled } so callers can skip the refresh when the user backs out.
  async function importModelWithWizard({ path = '', name = '', unload = null, defaultMode = 'copy' } = {}) {
    const mode = await openImportToConsoleWizard({ path, name, defaultMode });
    if (!mode) return { canceled: true };
    if (!path) {
      toast('This model has no file path to import', false);
      return { canceled: true };
    }
    const label = name || String(path).split(/[\\/]/).pop() || 'model';
    const progressId = newImportProgressId();
    const stopPolling = startImportProgressPolling(progressId, { mode });
    setImportProgressUi({
      title: mode === 'move' ? `Moving ${label}` : `Importing ${label}`,
      text: importProgressPhaseText('unloading', mode),
      hint: 'Large GGUF files can take a minute to copy.',
      indeterminate: true,
    });
    let data;
    try {
      if (unload && (unload.pid || unload.api_url)) {
        setImportProgressUi({
          title: mode === 'move' ? `Moving ${label}` : `Importing ${label}`,
          text: importProgressPhaseText('unloading', mode),
          indeterminate: true,
        });
        await unloadExternalModel(unload);
      }
      setImportProgressUi({
        title: mode === 'move' ? `Moving ${label}` : `Importing ${label}`,
        text: importProgressPhaseText(mode === 'move' ? 'moving' : 'copying', mode),
        progress: 0,
      });
      data = await api('/api/models/import-into-console', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, mode, overwrite: false, progress_id: progressId }),
        timeoutMs: 0,
      });
    } catch (err) {
      stopPolling();
      closeImportProgressUi();
      toast(err.message || 'Could not import model into Console library', false);
      return { canceled: true, error: err };
    }

    // The model already exists in the Console library — never create a silent
    // duplicate. Ask whether to overwrite the existing copy or abort.
    if (data?.exists) {
      stopPolling();
      closeImportProgressUi();
      const overwrite = await openConfirmDialog({
        title: 'Model already exists',
        message: `${label} is already in the Flash Console library. Overwrite the existing copy?`,
        sub: data.existing_path || path,
        confirmLabel: 'Overwrite',
        cancelLabel: 'Abort',
        kicker: 'Import model',
      });
      if (!overwrite) {
        stopPolling();
        closeImportProgressUi();
        toast('Import cancelled — the model already exists in Flash Console');
        return { canceled: true };
      }
      setImportProgressUi({
        title: mode === 'move' ? `Moving ${label}` : `Importing ${label}`,
        text: importProgressPhaseText(mode === 'move' ? 'moving' : 'copying', mode),
        progress: 0,
      });
      const retryProgressId = newImportProgressId();
      const stopRetryPolling = startImportProgressPolling(retryProgressId, { mode });
      try {
        data = await api('/api/models/import-into-console', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, mode, overwrite: true, progress_id: retryProgressId }),
          timeoutMs: 0,
        });
      } catch (err) {
        stopRetryPolling();
        closeImportProgressUi();
        toast(err.message || 'Could not overwrite the model', false);
        return { canceled: true, error: err };
      } finally {
        stopRetryPolling();
      }
    }

    stopPolling();
    setImportProgressUi({
      title: mode === 'move' ? `Moving ${label}` : `Importing ${label}`,
      text: importProgressPhaseText('refreshing', mode),
      indeterminate: true,
    });

    // Refresh the library + Engines dropdown and verify the copy is there.
    const libraryPath = data?.library_path || data?.path || '';
    const verified = await refreshImportedModelViews(libraryPath, path);
    if (verified) {
      toast(`${mode === 'move' ? 'Moved' : 'Imported'} ${label} into Flash Console`);
    } else if (data?.success) {
      toast(`${label} copied to Flash Console — still updating the model list…`);
      void refresh({ forceCatalogRefresh: true, rebindInspector: true });
    } else {
      toast(`Could not confirm ${label} in the model library`, false);
    }
    closeImportProgressUi();
    return { canceled: false, data, mode, verified };
  }

  window.DFlashModelsLive = {
    refresh,
    selectModel,
    loadModel,
    waitForFreeTokenReady,
    getLoadEngine,
    setLoadEngine,
    isHfEngineModel,
    setTypeFilter,
    modelHasReasoning,
    revealModelFromEngineCard,
    findModelForDownload,
    ensureModelForDownload,
    renderLoadActions: stackActionButton,
    modelKey,
    unloadModel,
    openImportToConsoleWizard,
    importModelWithWizard,
    isModelAlreadyImported,
  };
})();
