/** Live Server tab — polls /api/servers and renders model stack cards */
(function () {
  const { api, toast, setSelectLoading } = window.ConsoleApi;

  const SPEC_PROFILES = new Set(['gemma-chat', 'gemma-12-dflash', 'qwen-dflash', 'bonsai-spec']);
  const PROFILE_CTX_MAX = {
    'gemma-chat': 262144,
    'gemma-ar': 262144,
    'gemma-12-dflash': 262144,
    'qwen-dflash': 32768,
    'qwen-ar': 32768,
    'bonsai': 8192,
    'bonsai-spec': 16384,
    'nomic-embed': 2048,
  };
  const EMBEDDING_PROFILES = new Set(['nomic-embed']);

  let servers = [];
  let allServers = [];
  let externalGpuLoads = [];
  let gpus = [];
  let showDflashEngines = true;
  let showExternalEngines = true;
  let engineCardsFilter = 'both';
  let engineFiltersReady = false;

  const ENGINE_FILTER_CYCLE = ['both', 'dflash', 'external'];
  const ENGINE_FILTER_LABELS = {
    both: 'All models',
    dflash: 'This app',
    external: 'External apps',
  };
  let activeId = localStorage.getItem('dflashConsole.activeServerId') || '';
  let pollTimer = null;
  const serverActions = new Map();
  const pendingLoads = new Map();
  const PREFS_KEY = 'dflashConsole.modelPrefs';
  let catalogModels = [];
  let suppressRunningToggle = false;
  let selectedLoadedKey = localStorage.getItem('dflashConsole.selectedLoadedKey') || '';
  let selectedModelKey = '';
  let currentLoadPlan = null;
  let currentLoadPlanKey = '';
  let loadPlanRequestKey = '';
  const ENGINE_MODEL_PLACEHOLDER = 'Model to load';
  let statusFetchPending = false;
  let initialStatusSettled = false;
  let pollInFlight = false;
  let latestStatusRevision = 0;
  let externalFetchPending = false;
  let externalInitialFetchDone = false;
  let externalMissingPolls = 0;
  let externalPollCounter = 0;

  const MODEL_GROUPS = window.DFlashModelGroups?.GROUPS || [
    { id: 'dflash', label: 'DFlash' },
    { id: 'llm', label: 'LLM' },
  ];
  let inspectorBound = null;
  let inspectorFilling = false;
  let inspectorDirty = false;
  let inspectorLoadedOnGpu = false;
  let inspectorPendingReload = false;
  let inspectorActiveTab = 'info';
  let autoSaveTimer = null;
  let saveInFlight = null;
  let logsFollowTail = true;
  let logsScrollBound = false;
  let logsFilterBound = false;
  let logsJumpObserver = null;
  let lastLogsServerId = '';
  let logLinesRaw = [];
  let logFilterId = localStorage.getItem('dflashConsole.logFilter') || 'all';
  const LOG_FETCH_TAIL = 500;
  const LOG_SCROLL_THRESHOLD = 32;

  function logsAtBottom(box) {
    if (!box) return true;
    return box.scrollHeight - box.scrollTop - box.clientHeight <= LOG_SCROLL_THRESHOLD;
  }

  function bindLogsFilterDropdown() {
    const trigger = document.getElementById('serverLogsFilterTrigger');
    const menu = document.getElementById('serverLogsFilterMenu');
    const label = document.getElementById('serverLogsFilterLabel');
    if (!trigger || !menu || logsFilterBound) return;
    logsFilterBound = true;

    function syncFilterUi() {
      const filterLabel = window.DFlashLogFormat?.filterLabel?.(logFilterId) || 'All lines';
      if (label) label.textContent = filterLabel;
      menu.querySelectorAll('.lm-logs-filter-item').forEach((item) => {
        const active = item.dataset.filter === logFilterId;
        item.classList.toggle('active', active);
        item.setAttribute('aria-selected', active ? 'true' : 'false');
      });
    }

    function closeMenu() {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    }

    function positionMenu() {
      const rect = trigger.getBoundingClientRect();
      menu.style.minWidth = `${Math.max(rect.width, 170)}px`;
      menu.style.left = `${Math.max(8, rect.left)}px`;
      menu.style.top = `${Math.max(8, rect.top - menu.offsetHeight - 4)}px`;
    }

    function openMenu() {
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      positionMenu();
    }

    function setLogFilter(nextId, shouldRender = true) {
      logFilterId = nextId || 'all';
      localStorage.setItem('dflashConsole.logFilter', logFilterId);
      syncFilterUi();
      closeMenu();
      if (shouldRender) renderLogs(logLinesRaw);
    }

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      if (menu.hidden) openMenu();
      else closeMenu();
    });

    menu.addEventListener('click', (e) => e.stopPropagation());

    menu.querySelectorAll('.lm-logs-filter-item').forEach((item) => {
      item.addEventListener('click', () => setLogFilter(item.dataset.filter || 'all'));
    });

    document.addEventListener('click', closeMenu);
    window.addEventListener('resize', closeMenu);

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeMenu();
    });

    setLogFilter(logFilterId, false);
  }

  function positionLogsJumpButton() {
    const box = document.getElementById('serverLogsBody');
    const btn = document.getElementById('serverLogsJumpBottom');
    if (!box || !btn) return;
    const rect = box.getBoundingClientRect();
    const scrollbarWidth = Math.max(0, box.offsetWidth - box.clientWidth);
    const btnSize = btn.offsetWidth || 34;
    const gap = 8;
    btn.style.left = `${Math.max(12, rect.right - scrollbarWidth - btnSize - gap)}px`;
    btn.style.top = `${Math.max(12, rect.bottom - btnSize - gap)}px`;
  }

  function updateLogsJumpButton() {
    const box = document.getElementById('serverLogsBody');
    const btn = document.getElementById('serverLogsJumpBottom');
    if (!box || !btn) return;
    const show = box.scrollHeight > box.clientHeight + 8 && !logsAtBottom(box);
    btn.classList.toggle('is-visible', show);
    if (show) positionLogsJumpButton();
  }

  function scrollLogsToBottom() {
    const box = document.getElementById('serverLogsBody');
    if (!box) return;
    logsFollowTail = true;
    box.scrollTop = box.scrollHeight;
    window.requestAnimationFrame(updateLogsJumpButton);
  }

  function ensureLogsJumpSentinel() {
    const box = document.getElementById('serverLogsBody');
    if (!box) return;
    let sentinel = box.querySelector('.lm-logs-jump-sentinel');
    if (!sentinel) {
      sentinel = document.createElement('div');
      sentinel.className = 'lm-logs-jump-sentinel';
      sentinel.setAttribute('aria-hidden', 'true');
    }
    box.appendChild(sentinel);
    if (!logsJumpObserver) {
      logsJumpObserver = new IntersectionObserver(
        ([entry]) => {
          if (entry) logsFollowTail = entry.isIntersecting;
          updateLogsJumpButton();
        },
        { root: box, threshold: 0 },
      );
    }
    logsJumpObserver.disconnect();
    logsJumpObserver.observe(sentinel);
  }

  function bindLogsAutoScroll() {
    const box = document.getElementById('serverLogsBody');
    if (!box || logsScrollBound) return;
    logsScrollBound = true;
    box.addEventListener('scroll', () => {
      logsFollowTail = logsAtBottom(box);
      updateLogsJumpButton();
    }, { passive: true });
    window.addEventListener('resize', updateLogsJumpButton);
    document.getElementById('serverLogsJumpBottom')?.addEventListener('click', scrollLogsToBottom);
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function getServerAction(serverId) {
    return serverId ? (serverActions.get(serverId) || null) : null;
  }

  function setServerAction(serverId, action) {
    if (!serverId) return;
    if (action) serverActions.set(serverId, action);
    else serverActions.delete(serverId);
    updateEnginePageNotice();
  }

  function isServerBusy(serverId) {
    return !!getServerAction(serverId);
  }

  function canLoadModel(model) {
    if (!model) return false;
    const serverId = model.server_id || activeServer()?.id;
    if (!serverId) return false;
    if (isServerBusy(serverId)) return false;
    const server = servers.find((s) => s.id === serverId) || allServers.find((s) => s.id === serverId);
    if (server?.status === 'booting' || server?.booting) return false;
    if (model.loadable && model.server_id) return true;
    return !!model.path;
  }

  function anyServerLoading() {
    return pendingLoads.size > 0 || servers.some((s) => s.status === 'booting' || s.booting);
  }

  function pendingLoadRow(serverId) {
    const meta = pendingLoads.get(serverId);
    if (!meta) return null;
    return {
      card_state: 'loading',
      title: meta.label,
      label: meta.label,
      role: 'alias',
      ejectable: true,
      progress: null,
      plain_llm: !!meta.plain_gguf,
    };
  }

  function syncPendingLoadsFeed() {
    const map = {};
    for (const [serverId, meta] of pendingLoads.entries()) {
      map[serverId] = { label: meta.label || meta };
    }
    window.DFlashStatusFeed?.setPendingLoads?.(map);
  }

  function activeServer() {
    return servers.find((s) => s.id === activeId) || allServers.find((s) => s.id === activeId) || servers[0] || null;
  }

  function serverIsLive(server) {
    if (!server) return false;
    const action = getServerAction(server.id);
    if (action === 'stopping') return false;
    if (action === 'ejecting') {
      return server.running || server.status === 'booting' || server.status === 'loaded';
    }
    if (action === 'starting') return true;
    // The live status payload is authoritative while a router is starting or
    // already listening. Do not let a stale persisted engine_on flag make the
    // toggle disagree with the "Engine: Running" label.
    return server.running || server.status === 'booting' || server.status === 'loaded';
  }

  /** Follow backend when another profile was started via API while the UI had a stopped selection. */
  function syncActiveIdFromLiveState() {
    if (isServerBusy(activeId)) return;
    if (getServerAction(activeId) === 'stopping') return;
    if (serverIsLive(activeServer())) return;
    const live = servers.find((s) => serverIsLive(s));
    if (live && live.id !== activeId) {
      activeId = live.id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
  }

  function anyServerGenerating() {
    for (const server of servers) {
      const stats = server?.inference_stats || {};
      if (stats.generating) return true;
      if (Array.isArray(stats.slots) && stats.slots.some((slot) => slot?.generating)) return true;
    }
    for (const row of externalGpuLoads) {
      const stats = row?.inference_stats || {};
      if (stats.generating) return true;
      if (Array.isArray(stats.slots) && stats.slots.some((slot) => slot?.generating)) return true;
    }
    return false;
  }

  function pollIntervalMs() {
    if (anyServerGenerating()) return 500;
    if (serverActions.size > 0 || anyServerLoading() || loadedServerCount() > 0) return 1000;
    return 2500;
  }

  function reschedulePoll() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = window.setInterval(() => void pollTick(), pollIntervalMs());
  }

  function serversStatusUrl(includeExternal = false, fresh = false) {
    const params = new URLSearchParams();
    if (!includeExternal) params.set('include_external', '0');
    if (fresh) params.set('fresh', '1');
    const query = params.toString();
    return query ? `/api/servers?${query}` : '/api/servers';
  }

  function hasVisibleGpuCards() {
    return filterLoadedEntries(collectLoadedEntries()).length > 0;
  }

  function gpuCardsSectionReady() {
    if (!initialStatusSettled || !catalogLoaded) return false;
    if (hasVisibleGpuCards()) return true;
    return externalInitialFetchDone;
  }

  function updateEnginePageNotice() {
    const banner = document.getElementById('enginePageNotice');
    const titleEl = document.getElementById('enginePageNoticeTitle');
    const detailEl = document.getElementById('enginePageNoticeDetail');
    if (!banner || !titleEl || !detailEl) return;

    const server = activeServer();
    const action = server ? getServerAction(server.id) : '';
    const bootingCount = bootingServerCount();
    const startingCount = [...serverActions.values()].filter((value) => value === 'starting').length;
    const loadingCount = pendingLoads.size;

    let title = '';
    let detail = '';
    let mode = 'loading';

    if (!initialStatusSettled && (!catalogLoaded || statusFetchPending)) {
      title = 'Loading engine status…';
      detail = 'Checking llama-server listeners and loaded models. The first load after startup can take a little longer.';
      mode = 'loading';
    } else if (!gpuCardsSectionReady()) {
      title = 'Loading Loaded on GPU…';
      detail = externalFetchPending
        ? 'Scanning the GPU for DFlash and external app models. Cards will appear here when ready.'
        : 'Finishing the GPU scan so loaded models can appear in this section.';
      mode = 'loading';
    } else if (action === 'starting' || startingCount > 0) {
      title = startingCount > 1 ? `Starting ${startingCount} engines…` : 'Starting engine…';
      detail = 'The llama-server process is launching. Cards and chat stay disabled until the listener is up.';
      mode = 'starting';
    }

    if (!title) {
      banner.classList.add('hidden');
      banner.classList.remove('is-starting');
      return;
    }

    banner.classList.remove('hidden');
    banner.classList.toggle('is-starting', mode === 'starting');
    titleEl.textContent = title;
    detailEl.textContent = detail;
  }

  function applyServersPayload(data, { mergeExternal = true } = {}) {
    const revision = Number(data?.snapshot_revision || 0);
    if (revision > 0 && latestStatusRevision > 0 && revision < latestStatusRevision) {
      return false;
    }
    if (revision > 0) latestStatusRevision = revision;
    servers = data.servers || [];
    allServers = data.all_servers || servers;
    if (mergeExternal) {
      mergeExternalGpuLoads(data.external_gpu_loads);
    }
    gpus = data.gpus || gpus;
    return true;
  }

  function mergeExternalGpuLoads(rows) {
    const next = Array.isArray(rows) ? rows : [];
    if (next.length) {
      externalGpuLoads = next;
      externalMissingPolls = 0;
      return;
    }
    if (!externalGpuLoads.length) return;
    externalMissingPolls += 1;
    if (externalMissingPolls > 2) {
      externalGpuLoads = [];
      externalMissingPolls = 0;
    }
  }

  async function refreshExternalGpuLoads(shouldRender = true) {
    if (externalFetchPending) return;
    externalFetchPending = true;
    updateEnginePageNotice();
    try {
      const data = await api('/api/servers?include_external=1');
      mergeExternalGpuLoads(data.external_gpu_loads);
      if (shouldRender) renderCards();
    } catch {
      /* keep previous external cards */
    } finally {
      externalFetchPending = false;
      externalInitialFetchDone = true;
      updateEnginePageNotice();
    }
  }

  async function captureConsoleBoot() {
    try {
      await api('/api/health');
    } catch {
      /* ignore */
    }
  }

  function modelKeyFor(model) {
    return model?.server_id || model?.path || model?.id || '';
  }

  function loadBrowsePrefs() {
    try {
      return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
    } catch {
      return {};
    }
  }

  function saveBrowsePrefs(prefs) {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  }

  function syncServerFromPatch(serverId, patch) {
    for (const list of [allServers, servers]) {
      const idx = list.findIndex((s) => s.id === serverId);
      if (idx < 0) continue;
      list[idx] = {
        ...list[idx],
        ...patch,
        load_settings: { ...(list[idx].load_settings || {}), ...(patch.load_settings || {}) },
        inference_settings: { ...(list[idx].inference_settings || {}), ...(patch.inference_settings || {}) },
      };
    }
  }

  function getMergedLoadSettings(model) {
    const profile = model?.profile || '';
    const ctxMax = model?.context_max || PROFILE_CTX_MAX[profile] || 262144;
    const gpuMax = model?.gpu_layers_max || 128;
    const base = {
      profile,
      context_max: ctxMax,
      gpu_layers_max: gpuMax,
      context_size: model?.context_size || 65536,
      load_settings: { ...(model?.load_settings || {}) },
      inference_settings: { ...(model?.inference_settings || {}) },
    };
    if (model?.server_id) {
      const server = allServers.find((s) => s.id === model.server_id) || servers.find((s) => s.id === model.server_id);
      if (server) {
        return {
          ...base,
          ...server,
          context_max: ctxMax,
          gpu_layers_max: gpuMax,
          load_settings: { ...(server.load_settings || {}) },
          inference_settings: { ...(server.inference_settings || {}) },
        };
      }
    }
    const prefs = loadBrowsePrefs()[modelKeyFor(model)];
    if (prefs) {
      return {
        ...base,
        context_size: prefs.context_size ?? base.context_size,
        load_settings: { ...base.load_settings, ...(prefs.load_settings || {}) },
        inference_settings: { ...base.inference_settings, ...(prefs.inference_settings || {}) },
      };
    }
    return base;
  }

  async function persistInspectorSettings() {
    if (!inspectorBound || inspectorFilling) return;
    const patch = readInspectorLoadSettings();
    if (inspectorBound.serverId) {
      await api(`/api/servers/${encodeURIComponent(inspectorBound.serverId)}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
      syncServerFromPatch(inspectorBound.serverId, patch);
    } else if (inspectorBound.modelKey) {
      const prefs = loadBrowsePrefs();
      prefs[inspectorBound.modelKey] = patch;
      saveBrowsePrefs(prefs);
    }
    inspectorDirty = false;
    if (inspectorLoadedOnGpu) inspectorPendingReload = true;
    updateInspectorReloadNotice();
    const selected = selectedCatalogModel();
    if (selected) void refreshLoadPlan(selected);
    window.DFlashStatusFeed?.note('Runtime settings saved', 'Reload the model to apply changes');
  }

  function isModelLoadedOnGpu(model) {
    if (!model) return false;
    if (model.external || model.loaded_on_gpu) return true;
    const serverId = model.server_id || '';
    if (!serverId) return false;
    const server = servers.find((s) => s.id === serverId);
    return server?.status === 'loaded';
  }

  function syncInspectorLoadedState(model) {
    inspectorLoadedOnGpu = isModelLoadedOnGpu(model);
    if (!inspectorLoadedOnGpu) inspectorPendingReload = false;
    updateInspectorReloadNotice();
    syncInspectorRuntimeAvailability(model);
  }

  function updateInspectorReloadNotice() {
    const el = document.getElementById('inspectorReloadNotice');
    const button = document.getElementById('inspectorReloadBtn');
    if (!el) return;
    el.classList.toggle('hidden', !(inspectorLoadedOnGpu && inspectorPendingReload));
    if (button) {
      button.disabled = !inspectorLoadedOnGpu
        || !inspectorPendingReload
        || isServerBusy(inspectorBound?.serverId);
    }
  }

  function syncInspectorRuntimeAvailability(model) {
    const external = !!model?.external;
    document.getElementById('inspectorExternalRuntimeNote')?.classList.toggle('hidden', !external);
    document.querySelector('[data-inspector-panel="load"]')?.classList.toggle('read-only-external', external);
  }

  function clearInspectorPendingReload() {
    inspectorPendingReload = false;
    updateInspectorReloadNotice();
  }

  async function reloadInspectorModel() {
    const serverId = inspectorBound?.serverId || '';
    if (!serverId || !inspectorLoadedOnGpu || !inspectorPendingReload) return;
    const server = servers.find((item) => item.id === serverId)
      || allServers.find((item) => item.id === serverId);
    if (!server || isServerBusy(serverId)) return;
    const selected = selectedCatalogModel();
    const row = loadedRowsForServer(server)[0] || {};
    const model = selected?.server_id === serverId
      ? selected
      : modelFromLoadedEntry(server, row);
    if (!model) {
      toast('The loaded model could not be identified', false);
      return;
    }
    const button = document.getElementById('inspectorReloadBtn');
    if (button) button.disabled = true;
    try {
      await flushInspectorSave();
      const unloaded = await ejectServer(serverId);
      if (!unloaded) return;
      await loadModelOnServer(serverId, { ...model, server_id: serverId });
    } finally {
      updateInspectorReloadNotice();
    }
  }

  async function flushInspectorSave() {
    if (autoSaveTimer) {
      clearTimeout(autoSaveTimer);
      autoSaveTimer = null;
    }
    if (saveInFlight) await saveInFlight;
    if (!inspectorBound || inspectorFilling) return;
    saveInFlight = persistInspectorSettings().finally(() => {
      saveInFlight = null;
    });
    await saveInFlight;
  }

  function scheduleInspectorAutoSave() {
    if (inspectorFilling || !inspectorBound) return;
    inspectorDirty = true;
    if (inspectorLoadedOnGpu) inspectorPendingReload = true;
    updateInspectorReloadNotice();
    if (autoSaveTimer) clearTimeout(autoSaveTimer);
    autoSaveTimer = window.setTimeout(() => {
      autoSaveTimer = null;
      saveInFlight = persistInspectorSettings()
        .catch((err) => toast(err.message, false))
        .finally(() => {
          saveInFlight = null;
        });
    }, 400);
  }

  let cardContextTarget = null;

  function catalogModelForServer(serverId) {
    if (!serverId) return null;
    return catalogModels.find((m) => m.server_id === serverId)
      || catalogModels.find((m) => modelCatalogKey(m) === serverId)
      || null;
  }

  function normalizeModelPath(path) {
    return String(path || '').replace(/\\/g, '/').trim().toLowerCase();
  }

  /** True when the picked file is not this engine profile's configured checkpoint. */
  function shouldSendModelPath(model, serverId) {
    if (!model?.path) return false;
    if (model.plain_gguf) return true;
    const profile = catalogModelForServer(serverId);
    if (!profile?.path) return true;
    if (normalizeModelPath(profile.path) !== normalizeModelPath(model.path)) return true;
    return !(model.server_id && model.server_id === serverId);
  }

  function inspectorModelTitle(model) {
    if (!model) return 'Model';
    return model.display_name_full
      || model.display_name
      || model.label
      || model.id
      || 'Model';
  }

  function modelFromLoadedEntry(server, row) {
    const title = cardDisplayName(row, server);
    const displayFields = {
      label: title || server?.label || server?.id || 'Model',
      display_name: row?.display_name || server?.display_name || null,
      display_name_full: row?.display_name_full || server?.display_name_full || null,
    };
    if (row?.external || server?.external) {
      return {
        id: `external-${row.pid}`,
        ...displayFields,
        path: row.model_path || '',
        size_gb: cardSizeGb(row),
        vram_gb: row.vram_gb,
        gpu_display: row.gpu_display || server.gpu_display || '',
        app_label: row.app_label || 'External app',
        listen_port: row.listen_port || null,
        external: true,
        loaded_on_gpu: true,
        loadable: false,
        arch: '—',
        params: '—',
        quant: row.quant || '—',
        context_max: null,
        capabilities: [],
      };
    }
    const catalog = catalogModelForServer(server.id);
    if (catalog) {
      return {
        ...catalog,
        ...displayFields,
        label: title || catalog.display_name_full || catalog.display_name || catalog.label || catalog.id,
        display_name_full: displayFields.display_name_full || catalog.display_name_full || null,
        display_name: displayFields.display_name || catalog.display_name || null,
        loaded_on_gpu: server.status === 'loaded',
        vram_gb: row.vram_gb ?? catalog.vram_gb,
        gpu_display: row.gpu_display || server.gpu_display,
      };
    }
    return {
      id: server.id,
      server_id: server.id,
      ...displayFields,
      profile: server.profile || '',
      path: row.path || '',
      size_gb: cardSizeGb(row),
      vram_gb: row.vram_gb,
      gpu_display: server.gpu_display || row.gpu_display || '',
      listen_port: server.port,
      loaded_on_gpu: server.status === 'loaded',
      loadable: true,
      arch: row.arch || '—',
      params: row.params || '—',
      quant: row.quant || '—',
      context_max: PROFILE_CTX_MAX[server.profile] || server.context_max || 262144,
      gpu_layers_max: server.gpu_layers_max || 128,
      capabilities: row.capabilities || [],
    };
  }

  function ensureInspectorVisible() {
    try {
      if (localStorage.getItem('dflashConsole.inspectorCollapsed') === '1') return;
    } catch {
      /* ignore */
    }
    if (window.DFlashUiLayout?.getBool?.('inspector_collapsed', false)) return;
    const bodyEl = document.querySelector('.lm-body');
    const inspectorToggleBtn = document.querySelector('[data-action="toggle-inspector"]');
    if (!bodyEl?.classList.contains('inspector-collapsed')) return;
    bodyEl.classList.remove('inspector-collapsed');
    inspectorToggleBtn?.classList.add('active');
  }

  function currentInspectorTab() {
    const active = document.querySelector('.lm-inspector-tab.active');
    const tabId = active?.dataset?.inspectorTab;
    if (tabId === 'info' || tabId === 'load') return tabId;
    return inspectorActiveTab === 'load' ? 'load' : 'info';
  }

  function rememberInspectorTab(tabId) {
    if (tabId !== 'info' && tabId !== 'load') return;
    inspectorActiveTab = tabId;
    window.DFlashUiLayout?.setString?.('inspector_tab', tabId);
  }

  function focusInspectorTab(tabId) {
    if (tabId !== 'info' && tabId !== 'load') return;
    rememberInspectorTab(tabId);
    const tab = document.querySelector(`.lm-inspector-tab[data-inspector-tab="${tabId}"]`);
    if (!tab) return;
    document.querySelectorAll('.lm-inspector-tab').forEach((t) => t.classList.toggle('active', t === tab));
    document.querySelectorAll('.lm-inspector-panel').forEach((p) => {
      p.classList.toggle('active', p.dataset.inspectorPanel === tabId);
    });
  }

  async function selectLoadedCard(server, row, { tab = null } = {}) {
    if (!server || !row) return;
    selectedLoadedKey = loadedCardKey(server, row);
    localStorage.setItem('dflashConsole.selectedLoadedKey', selectedLoadedKey);
    if (!row.external && !server.external) {
      activeId = server.id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
    const model = modelFromLoadedEntry(server, row);
    if (row.external || server.external) {
      resetEngineModelPicker();
    }
    await applyModelSelection(model);
    ensureInspectorVisible();
    focusInspectorTab(tab ?? currentInspectorTab());
    renderCards();
    if (!row.external && !server.external) renderToolbar(activeServer());
  }

  function hideCardContextMenu() {
    const menu = document.getElementById('serverCardContextMenu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML = '';
    cardContextTarget = null;
  }

  function openCardContextMenu(event, server, row) {
    const menu = document.getElementById('serverCardContextMenu');
    if (!menu) return;
    cardContextTarget = { server, row };
    const ready = row.card_state === 'ready';
    const loading = row.card_state === 'loading';
    const url = server.reachable_url || '';
    const path = row.path || '';
    const isEmbedding = server.engine_mode === 'embedding'
      || server.model_kind === 'embedding'
      || row.model_kind === 'embedding'
      || EMBEDDING_PROFILES.has(server.profile);

    menu.innerHTML = `
      <button type="button" data-cmd="details">Show details</button>
      <button type="button" data-cmd="runtime">Show runtime settings</button>
      <button type="button" data-cmd="copy-url"${url ? '' : ' disabled'}>Copy API URL</button>
      <button type="button" data-cmd="copy-path"${path ? '' : ' disabled'}>Copy model path</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <hr>
      <button type="button" data-cmd="unload"${ready && row.ejectable ? '' : ' disabled'} title="${isEmbedding ? 'Stop embedding engine and unload its model' : 'Unload model'}">Unload</button>
      <button type="button" data-cmd="cancel"${loading && row.ejectable ? '' : ' disabled'}>Cancel load</button>`;

    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;

    menu.querySelectorAll('button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        void runCardContextCommand(btn.dataset.cmd, server, row);
        hideCardContextMenu();
      });
    });
  }

  async function runCardContextCommand(cmd, server, row) {
    if (cmd === 'details') {
      await selectLoadedCard(server, row, { tab: 'info' });
      return;
    }
    if (cmd === 'runtime') {
      await selectLoadedCard(server, row, { tab: 'load' });
      return;
    }
    if (cmd === 'copy-url') {
      const url = server.reachable_url;
      if (!url) return;
      await navigator.clipboard.writeText(url);
      toast('API URL copied');
      return;
    }
    if (cmd === 'copy-path') {
      const path = row.path;
      if (!path) return;
      await navigator.clipboard.writeText(path);
      toast('Model path copied');
      return;
    }
    if (cmd === 'metadata') {
      const modal = document.getElementById('modelMetadataModal');
      const pre = document.getElementById('modelMetadataBody');
      if (pre) {
        pre.textContent = JSON.stringify({ server: { id: server.id, label: server.label, port: server.port, status: server.status, reachable_url: server.reachable_url }, row }, null, 2);
      }
      modal?.classList.add('open');
      modal?.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      return;
    }
    if (cmd === 'unload') {
      if (server.engine_mode === 'embedding' || server.model_kind === 'embedding' || row.model_kind === 'embedding' || EMBEDDING_PROFILES.has(server.profile)) {
        await stopServer(server.id);
        return;
      }
      await ejectServer(server.id);
      return;
    }
    if (cmd === 'cancel') {
      await stopServer(server.id);
    }
  }

  function loadedServerCount() {
    return servers.filter((s) => s.status === 'loaded').length + externalGpuLoads.length;
  }

  function fallbackLoadedRow(server, modelId) {
    const token = String(modelId || '').trim();
    const normalized = token.replace(/\\/g, '/');
    const basename = normalized.split('/').pop() || token;
    return {
      role: 'loaded-model',
      id: token,
      model_id: token,
      label: basename || server?.label || 'Loaded model',
      title: basename || token.replace(/-/g, ' ') || server?.label || 'Loaded model',
      subtitle: token ? `API: ${token}` : '',
      path: /\.gguf$/i.test(basename) || normalized.includes('/') ? token : '',
      card_state: server?.status === 'booting' ? 'loading' : 'ready',
      progress: server?.load_progress ?? null,
      ejectable: true,
      plain_llm: true,
      is_adhoc: true,
      inference_stats: server?.inference_stats || {},
    };
  }

  function loadedRowsForServer(server) {
    const cards = Array.isArray(server?.visible_cards) ? server.visible_cards.slice() : [];
    const loaded = Array.isArray(server?.loaded_models) ? server.loaded_models : [];
    if (!loaded.length) return cards;
    const represented = new Set(
      cards
        .flatMap((row) => [row?.id, row?.model_id, row?.path, row?.model_path])
        .map((value) => String(value || '').replace(/\\/g, '/').trim().toLowerCase())
        .filter(Boolean),
    );
    for (const modelId of loaded) {
      const key = String(modelId || '').replace(/\\/g, '/').trim().toLowerCase();
      if (key && !represented.has(key)) {
        cards.push(fallbackLoadedRow(server, modelId));
        represented.add(key);
      }
    }
    return cards;
  }

  function entryForCard(card) {
    const serverId = card?.dataset?.serverId;
    const role = card?.dataset?.role;
    const externalPid = card?.getAttribute('data-external-pid');

    if (externalPid) {
      const pid = Number(externalPid);
      const row = externalGpuLoads.find((entry) => Number(entry.pid) === pid);
      if (!row) return null;
      return {
        server: {
          id: `external-${row.pid}`,
          label: row.app_label || 'External app',
          port: row.listen_port || '',
          external: true,
          status: 'loaded',
          gpu_display: row.gpu_display || '',
        },
        row,
      };
    }

    const server = servers.find((s) => s.id === serverId);
    if (!server) return null;
    let row = loadedRowsForServer(server).find((entry) => entry.role === role);
    if (!row) row = pendingLoadRow(serverId);
    return row ? { server, row } : null;
  }

  function loadedCardKey(server, row) {
    if (row?.external || server?.external) return `external-${row.pid}`;
    if (row?.role === 'loaded-model') return `${server?.id || ''}::${row.id || row.model_id || ''}`;
    return server?.id || '';
  }

  function bootingServerCount() {
    return servers.filter((s) => s.status === 'booting').length;
  }

  function collectLoadedEntries() {
    const entries = [];
    for (const server of servers) {
      const cards = loadedRowsForServer(server);
      for (const row of cards) {
        entries.push({ server, row });
      }
    }
    for (const row of externalGpuLoads) {
      entries.push({
        server: {
          id: `external-${row.pid}`,
          label: row.app_label || 'External app',
          port: row.listen_port || '',
          external: true,
          status: 'loaded',
          gpu_display: row.gpu_display || '',
          inference_stats: row.inference_stats || {},
        },
        row,
      });
    }
    let result = entries;
    for (const serverId of pendingLoads.keys()) {
      const server = servers.find((s) => s.id === serverId);
      const row = pendingLoadRow(serverId);
      if (!server || !row) continue;
      result = result.filter(({ server: entryServer }) => entryServer.id !== serverId);
      result.push({ server, row });
    }
    return result;
  }

  function isExternalEntry({ server, row }) {
    return !!(row?.external || server?.external);
  }

  function filterLoadedEntries(entries) {
    return entries.filter((entry) => (
      isExternalEntry(entry) ? showExternalEngines : showDflashEngines
    ));
  }

  function applyEngineCardsFilter(mode) {
    engineCardsFilter = ENGINE_FILTER_CYCLE.includes(mode) ? mode : 'both';
    showDflashEngines = engineCardsFilter === 'both' || engineCardsFilter === 'dflash';
    showExternalEngines = engineCardsFilter === 'both' || engineCardsFilter === 'external';
  }

  function syncEngineCardsSectionLabel() {
    const el = document.getElementById('engineCardsSectionLabel');
    if (!el) return;
    const count = collectLoadedEntries().length;
    if (count === 0) el.textContent = 'No models loaded on GPU';
    else if (count === 1) el.textContent = '1 model loaded on GPU';
    else el.textContent = `${count} models loaded on GPU`;
  }

  function syncEngineFilterButton() {
    const btn = document.getElementById('engineCardsFilterBtn');
    if (!btn) return;
    const label = ENGINE_FILTER_LABELS[engineCardsFilter] || ENGINE_FILTER_LABELS.both;
    btn.textContent = label;
    btn.classList.toggle('active', engineCardsFilter !== 'both');
    btn.title = `Showing: ${label}. Click to cycle.`;
  }

  async function initEngineFilters() {
    if (engineFiltersReady) return;
    engineFiltersReady = true;
    applyEngineCardsFilter('both');
    syncEngineFilterButton();
  }

  function cycleEngineCardsFilter() {
    const idx = ENGINE_FILTER_CYCLE.indexOf(engineCardsFilter);
    const next = ENGINE_FILTER_CYCLE[(idx + 1) % ENGINE_FILTER_CYCLE.length];
    applyEngineCardsFilter(next);
    syncEngineFilterButton();
    renderCards();
  }

  function serverStatusLabel(server) {
    if (!server) return 'Stopped';
    if (server.status === 'error') return server.boot_error || 'Error';
    if (server.status === 'loaded') return 'Loaded';
    if (server.status === 'booting') return 'Loading…';
    if (server.running) return 'Idle';
    return 'Stopped';
  }

  function aggregateStatusLabel() {
    const loaded = loadedServerCount();
    const booting = bootingServerCount();
    const starting = [...serverActions.values()].filter((a) => a === 'starting').length;
    const loading = pendingLoads.size;
    const stopping = [...serverActions.values()].filter((a) => a === 'stopping').length;
    const ejecting = [...serverActions.values()].filter((a) => a === 'ejecting').length;
    if (stopping === 1 && loaded === 0 && booting === 0) return 'Stopping…';
    if (stopping > 1) return `${stopping} engines stopping`;
    if (ejecting === 1 && loaded <= 1) return 'Unloading…';
    if (ejecting > 0) return `${ejecting} unloading · ${loaded} loaded`;
    if (starting === 1 && loaded === 0 && booting === 0) return 'Starting engine…';
    if (starting > 0) return `${starting} starting · ${loaded} loaded`;
    if (loading > 0 && booting > 0 && loaded > 0) return `${loaded} loaded · ${Math.max(loading, booting)} loading`;
    if (loading > 1 || booting > 1) return `${Math.max(loading, booting)} models loading`;
    if (loading === 1) {
      const [serverId] = pendingLoads.keys();
      const meta = pendingLoads.get(serverId);
      return meta?.label ? `Loading ${meta.label}…` : 'Loading model…';
    }
    if (booting === 1) return 'Loading model…';
    if (loaded >= 1) return 'Running';
    const active = activeServer();
    if (active?.running) return 'Running (idle)';
    if (active?.status === 'booting') return 'Loading…';
    return 'Stopped';
  }

  function detailBadge(source, role) {
    if (role === 'draft-dflash') return 'DFlash draft';
    if (role === 'draft-dspark') return 'dspark draft';
    if (source === 'lmstudio') return 'weights file';
    return 'component';
  }

  function formatCardGb(value, { vram = false } = {}) {
    if (value == null || value === '') return '';
    const num = Number(value);
    if (!Number.isFinite(num) || num <= 0) return '';
    const text = num % 1 === 0 ? String(num) : num.toFixed(2).replace(/\.?0+$/, '');
    return vram ? `${text} GB VRAM` : `${text} GB`;
  }

  function cardSizeGb(row) {
    if (row.size_gb != null) return row.size_gb;
    const details = Array.isArray(row.stack_details) ? row.stack_details : [];
    let total = 0;
    let found = false;
    for (const part of details) {
      if (part.size_gb == null) continue;
      total += Number(part.size_gb);
      found = true;
    }
    return found ? Math.round(total * 100) / 100 : null;
  }

  function cardMetaLine({ server, row }) {
    const parts = [];
    const port = row.external ? row.listen_port : server.port;
    if (port) parts.push(`:${port}`);
    const gpu = row.gpu_display || server.gpu_display;
    if (gpu) parts.push(gpu);
    const size = formatCardGb(cardSizeGb(row));
    if (size) parts.push(size);
    const vram = formatCardGb(row.vram_gb, { vram: true });
    if (vram) parts.push(vram);
    return parts.join(' · ');
  }

  function cardDisplayName(row, server) {
    const genericName = (value) => !value || /^default$/i.test(String(value).trim());
    const basename = (path) => {
      if (!path) return '';
      const base = String(path).split(/[/\\]/).pop();
      return genericName(base) ? '' : base;
    };
    if (row?.external) {
      const externalName = row.model_name
        || row.title
        || row.display_name
        || row.display_name_full
        || basename(row.model_path)
        || row.app_label
        || 'External model';
      return basename(externalName) || String(externalName);
    }
    if (row?.is_adhoc || row?.plain_llm) {
      if (row?.title && !genericName(row.title)) return row.title;
      const fromPath = basename(row?.path);
      if (fromPath) return fromPath;
      if (row?.id && !genericName(row.id)) return String(row.id).replace(/-/g, ' ');
    }
    if (row?.display_name_full && !genericName(row.display_name_full)) return row.display_name_full;
    if (server?.display_name_full && !genericName(server.display_name_full)) return server.display_name_full;
    if (row?.display_name && !genericName(row.display_name)) return row.display_name;
    if (server?.display_name && !genericName(server.display_name)) return server.display_name;
    const fromPath = basename(row?.path);
    if (fromPath) return fromPath;
    if (row?.title && !genericName(row.title)) return row.title;
    if (row.role === 'alias' && row.id && !genericName(row.id)) return row.id;
    if (row.role === 'draft-dflash' || row.role === 'draft-dspark') {
      return basename(row?.path) || row.label || row.id || 'draft';
    }
    return row.label || row.id || 'model';
  }

  function cardHoverTitle({ server, row }) {
    const lines = [cardDisplayName(row, server)];
    const meta = cardMetaLine({ server, row });
    if (meta) lines.push(meta);
    const loadedBy = row?.loaded_by || row?.app_label;
    if (loadedBy) lines.push(`Loaded by: ${loadedBy}`);
    if (row?.model_path) lines.push(`Path: ${row.model_path}`);
    else if (row?.path) lines.push(`Path: ${row.path}`);
    if (row.external) {
      if (row.command_line) lines.push(row.command_line);
      if (row.pid) lines.push(`PID ${row.pid}`);
      return lines.filter(Boolean).join('\n');
    }
    lines.push(`Engine: ${server.label || server.id}`);
    if (server.reachable_url) lines.push(`API: ${server.reachable_url}`);
    const details = Array.isArray(row.stack_details) ? row.stack_details : [];
    for (const part of details) {
      const size = part.size_gb != null ? ` · ${part.size_gb} GB` : '';
      lines.push(`${detailBadge(part.source, part.role)}: ${part.name || '—'}${size}`);
    }
    return lines.filter(Boolean).join('\n');
  }

  function cardAppLabel(row) {
    if (row?.external) return row.app_label || 'External app';
    return row?.app_label || 'DFlash Console';
  }

  function inferModelKind(row) {
    if (row?.model_kind && row?.model_kind_label) {
      return { kind: row.model_kind, label: row.model_kind_label };
    }
    const hay = `${row?.title || ''} ${row?.model_name || ''} ${row?.model_path || ''} ${row?.command_line || ''} ${row?.role || ''}`.toLowerCase();
    if (hay.includes('speak_stt') || hay.includes('whisper') || hay.includes('small.en')) {
      return { kind: 'stt', label: 'Speech-to-text' };
    }
    if (hay.includes('nomic-embed') || hay.includes('embed-text') || hay.includes('--embedding')) {
      return { kind: 'embedding', label: 'Embedding' };
    }
    if (hay.includes('onevoice ui server')) {
      return { kind: 'app', label: 'App server' };
    }
    if (hay.includes('.gguf') || row?.role === 'alias' || row?.role === 'target') {
      return { kind: 'llm', label: 'LLM' };
    }
    return null;
  }

  function dflashLogoLabel(label = 'DFlash') {
    const safeLabel = escapeHtml(label);
    return `<span class="lm-tag gold dflash-logo-label" role="img" aria-label="${safeLabel}" title="${safeLabel}"></span>`;
  }

  function acceleratorBadge(title = 'Draft accelerator; not a target model') {
    return `<span class="lm-tag orange" title="${escapeHtml(title)}">Accelerator</span>`;
  }

  function modelKindBadge(row) {
    const inferred = inferModelKind(row);
    if (!inferred) return '';
    if (inferred.kind === 'llm' && !row?.external) {
      return '';
    }
    if (inferred.kind === 'embedding' && !row?.external) {
      return '';
    }
    const tone = {
      stt: 'purple',
      embedding: 'teal',
      llm: 'cyan',
      tts: 'pink',
      ocr: 'yellow',
      app: 'gray',
      other: 'gray',
    }[inferred.kind] || 'gray';
    return `<span class="lm-tag ${tone} lm-tag-kind" title="Model type">${escapeHtml(inferred.label)}</span>`;
  }

  function cardUsesDflashStack(row) {
    if (row?.external) return false;
    if (row?.is_adhoc || row?.plain_llm || row?.dflash_stack === false) return false;
    if (row?.dflash_stack === true) return true;
    const details = Array.isArray(row.stack_details) ? row.stack_details : [];
    return details.some((part) => String(part?.role || '').startsWith('draft'));
  }

  function roleBadge(row) {
    if (row.external) {
      const label = escapeHtml(cardAppLabel(row));
      return `<span class="lm-tag orange lm-tag-external-app" title="Loaded outside DFlash Console">External · ${label}</span>`;
    }
    const loadedBy = row?.loaded_by || row?.app_label || '';
    const appBadge = loadedBy && !/^dflash\s+console$/i.test(String(loadedBy).trim())
      ? `<span class="lm-tag orange" title="Requested by ${escapeHtml(loadedBy)}">${escapeHtml(loadedBy)}</span>`
      : '';
    if (row.role === 'draft-dflash') {
      return `${dflashLogoLabel('DFlash accelerator')}${acceleratorBadge('DFlash draft accelerator; not a target model')}${appBadge}`;
    }
    if (row.role === 'draft-dspark') {
      return `<span class="lm-tag yellow" title="dspark draft accelerator">dspark draft</span>${acceleratorBadge('DSpark draft accelerator; not a target model')}${appBadge}`;
    }
    const kind = inferModelKind(row);
    if (kind?.kind === 'embedding') {
      return `<span class="lm-tag teal" title="Embedding model">Embedding</span>${appBadge}`;
    }
    if (kind?.kind === 'stt') {
      return `<span class="lm-tag purple" title="Speech-to-text">${escapeHtml(kind.label)}</span>${appBadge}`;
    }
    if (cardUsesDflashStack(row)) {
      return `${dflashLogoLabel('DFlash speculative decoding stack')}${appBadge}`;
    }
    if (row.card_state === 'ready' || row.card_state === 'loading' || row.role === 'alias' || kind?.kind === 'llm') {
      return `<span class="lm-tag cyan" title="Standard LLM checkpoint">LLM</span>${appBadge}`;
    }
    if (row.source === 'lmstudio') return `<span class="lm-tag blue">LM Studio</span>${appBadge}`;
    return `<span class="lm-tag blue" title="Managed by DFlash Console">LLM</span>${appBadge}`;
  }

  function inferCardDetail(row) {
    if (row?.external) {
      const name = cardDisplayName(row);
      const normalizedName = String(name || '').trim().toLowerCase();
      const raw = String(row.card_detail || row.subtitle || '').trim();
      const normalizedRaw = raw.toLowerCase();
      if (raw && normalizedRaw !== normalizedName && !normalizedRaw.includes(normalizedName)) {
        return raw;
      }
      const kind = inferModelKind(row);
      const parts = [];
      if (kind?.label) parts.push(kind.label);
      if (kind?.kind === 'llm') {
        parts.push('GGUF');
        const quant = (name.match(/Q\d[_A-Z0-9]*/i) || [])[0];
        if (quant) parts.push(quant.toUpperCase());
      }
      if (row.listen_port && kind?.kind === 'llm') parts.push(`port ${row.listen_port}`);
      return parts.join(' · ');
    }
    if (row?.card_detail) return row.card_detail;
    if (row?.subtitle && row.subtitle !== 'Loading…' && !/^API:/i.test(String(row.subtitle))) {
      return row.subtitle;
    }
    const kind = inferModelKind(row);
    const name = row?.model_name || row?.title || '';
    const hay = `${name} ${row?.model_path || ''} ${row?.command_line || ''}`.toLowerCase();
    if (kind?.kind === 'stt') {
      const engine = hay.includes('speak_stt') || hay.includes('faster-whisper') ? 'faster-whisper' : 'Whisper';
      return ['Whisper', engine, name].filter(Boolean).join(' · ');
    }
    if (kind?.kind === 'embedding') {
      const embed = row?.embedding_settings || {};
      const parts = ['Embedding model'];
      if (embed.model_family || hay.includes('nomic-embed')) {
        parts.push(`${embed.model_family || 'nomic-embed-text'} ${embed.model_version || 'v1.5'}`.trim());
      }
      if (embed.parameters) parts.push(embed.parameters);
      if (embed.embedding_dimensions || embed.dimensions) {
        parts.push(`${embed.embedding_dimensions || embed.dimensions} dims`);
      }
      const quant = embed.quantization || (name.match(/Q\d[_A-Z0-9]*/i) || [])[0];
      if (quant) parts.push(String(quant).toUpperCase());
      parts.push(`${embed.pooling || row?.pooling || 'mean'} pooling`);
      if (embed.api_path) parts.push(embed.api_path);
      return parts.join(' · ');
    }
    if (kind?.kind === 'llm') {
      const parts = ['GGUF'];
      const quant = (name.match(/Q\d[_A-Z0-9]*/i) || [])[0];
      if (quant) parts.push(quant.toUpperCase());
      return parts.join(' · ');
    }
    return '';
  }

  function cardDetailHtml(row) {
    const detail = inferCardDetail(row);
    if (!detail) return '';
    return `<span class="lm-model-card-detail" title="${escapeHtml(detail)}">${escapeHtml(detail)}</span>`;
  }

  function cardTagMetricsHtml(row) {
    const disk = formatCardGb(cardSizeGb(row));
    if (!disk) return '';
    const label = row.external ? 'Model' : 'Disk';
    return `<span class="lm-model-card-metric lm-model-card-tag-metric"><span class="lbl">${label}</span>${escapeHtml(disk)}</span>`;
  }

  function slotInferenceStats(stats) {
    if (Array.isArray(stats?.slots) && stats.slots.length) {
      return stats.slots.filter((slot) => (
        slot?.generating
        || slot?.generation_tokens != null
        || slot?.prompt_tokens != null
      ));
    }
    if (stats?.prompt_tokens != null || stats?.generation_tokens != null || stats?.generating) {
      return [{ slot_id: 0, ...stats }];
    }
    return [];
  }

  function tokenSummary(entry) {
    if (!entry) return '';
    const parts = [];
    if (entry.prompt_tokens != null) parts.push(`IN ${entry.prompt_tokens}`);
    if (entry.generation_tokens != null) parts.push(`OUT ${entry.generation_tokens}`);
    if (entry.tokens_per_second != null) parts.push(`SPEED ${entry.tokens_per_second} t/s`);
    return parts.join(' · ');
  }

  function recentCompletionsTitle(history) {
    const rows = Array.isArray(history) ? history.slice(0, 3) : [];
    if (!rows.length) return 'Last completion';
    return `Recent completions\n${rows.map((entry, index) => `${index + 1}. ${tokenSummary(entry)}`).join('\n')}`;
  }

  function cardTokenMetricGroup(slot, { live = false, recent = [] } = {}) {
    if (live) {
      const inputTok = slot.generating
        ? (slot.prefill_tokens ?? slot.prompt_tokens ?? 0)
        : 0;
      const outTok = slot.generating ? (slot.generating_tokens ?? 0) : 0;
      const speed = slot.generating ? (slot.generating_tokens_per_second ?? 0) : 0;
      return `
        <span class="lm-model-card-token-metric is-live lm-model-card-token-generating" title="Live generation">
          <span class="lbl">IN</span>${escapeHtml(String(inputTok))}
          <span class="lm-model-card-token-separator">·</span>
          <span class="lbl">OUT</span>${escapeHtml(String(outTok))}
          <span class="lm-model-card-token-separator">·</span>
          <span class="lbl">SPEED</span>${escapeHtml(String(speed))} t/s
        </span>`;
    }
    const parts = [];
    if (slot.prompt_tokens != null) parts.push(`IN ${slot.prompt_tokens}`);
    if (slot.generation_tokens != null) parts.push(`OUT ${slot.generation_tokens}`);
    if (slot.tokens_per_second != null) parts.push(`SPEED ${slot.tokens_per_second} t/s`);
    const text = parts.join(' · ');
    if (!text) return '';
    return `<span class="lm-model-card-token-metric lm-model-card-token-last" title="${escapeHtml(recentCompletionsTitle(recent))}"><span class="lbl">LAST</span>${escapeHtml(text)}</span>`;
  }

  function inferenceIsGenerating(stats) {
    return !!stats?.generating
      || (Array.isArray(stats?.slots) && stats.slots.some((slot) => slot?.generating));
  }

  function cardTokenMetricsRow({ server, row }) {
    const isExternal = !!(row?.external || server?.external);
    const stats = row?.inference_stats || server?.inference_stats || {};
    if (!isExternal && server?.status !== 'loaded' && !inferenceIsGenerating(stats)) return '';
    const slots = slotInferenceStats(stats);
    if (!slots.length) slots.push({ slot_id: 0, generating: false });
    const primarySlot = slots.find((slot) => slot?.generating) || slots[0];

    const groups = [];
    const metrics = [cardTokenMetricGroup(primarySlot, { live: true })];
    const hasLast = !primarySlot.generating
      && (primarySlot.prompt_tokens != null || primarySlot.generation_tokens != null);
    if (hasLast) {
      metrics.push(cardTokenMetricGroup(primarySlot, {
        live: false,
        recent: stats.recent_completions,
      }));
    }
    groups.push(`<span class="lm-model-card-slot-metric-group">${metrics.join('')}</span>`);

    if (!groups.length && stats.tokens_loaded != null) {
      groups.push(
        `<span class="lm-model-card-slot-metric-group"><span class="lm-model-card-token-metric dim"><span class="lbl">KV</span>${escapeHtml(String(stats.tokens_loaded))} tok</span></span>`,
      );
    }

    if (!groups.length) return '';
    const multi = groups.length > 1 ? ' has-multi-slots' : '';
    return `<div class="lm-model-card-center-row lm-model-card-token-row${multi}">${groups.join('')}</div>`;
  }

  function cardLoadingPlaceholderRow(loading) {
    if (!loading) return '';
    return '<div class="lm-model-card-center-row lm-model-card-token-row lm-model-card-loading-spacer" aria-hidden="true"></div>';
  }

  function cardCenterBlock({
    server,
    row,
    ready,
    loading,
    isGenerating,
    installedBadge,
    statusBadge,
  }) {
    const tokenRow = ready || isGenerating
      ? cardTokenMetricsRow({ server, row })
      : cardLoadingPlaceholderRow(loading);
    const hasTokenRow = !!tokenRow;
    return `
      <div class="lm-model-card-center${hasTokenRow ? ' has-token-row' : ''}">
        <div class="lm-model-card-center-row lm-model-card-title-row">
          <span class="lm-model-path">${escapeHtml(cardDisplayName(row, server))}</span>
          <span class="lm-model-card-labels">
            ${installedBadge}
            ${statusBadge}
            ${modelKindBadge(row)}
            ${roleBadge(row)}
            ${cardDetailHtml(row)}
          </span>
        </div>
        ${tokenRow}
      </div>`;
  }

  function emptyMessage(server) {
    const action = getServerAction(server?.id);
    if (action === 'stopping') return 'Stopping server…';
    if (action === 'ejecting') return 'Unloading model…';
    if (action === 'starting') return 'Starting engine…';
    if (action === 'loading' || server?.status === 'booting') return 'Loading model…';
    if (server?.status === 'error') return server.boot_error || 'Engine failed to start. Check logs or try Load again.';
    if (server?.status === 'running') return 'Engine is listening but no model is loaded. Click Load.';
    return 'Engine stopped. Turn it on or load a model.';
  }

  function renderCards() {
    const wrap = document.getElementById('serverModelCards');
    const empty = document.getElementById('serverEmptyState');
    if (!wrap || !empty) return;

    if (!gpuCardsSectionReady()) {
      wrap.innerHTML = '';
      empty.classList.add('hidden');
      updateEnginePageNotice();
      return;
    }

    const allEntries = collectLoadedEntries();
    const entries = filterLoadedEntries(allEntries);
    if (!entries.length) {
      wrap.innerHTML = '';
      if (allEntries.length) {
        empty.textContent = 'No models match the current filters.';
      } else {
        empty.textContent = emptyMessage(activeServer());
      }
      empty.classList.remove('hidden');
      updateEnginePageNotice();
      return;
    }
    empty.classList.add('hidden');

    wrap.innerHTML = entries.map(({ server, row }) => {
      const ready = row.card_state === 'ready';
      const loading = row.card_state === 'loading';
      const actionKey = loadedCardKey(server, row);
      const ejecting = getServerAction(actionKey) === 'ejecting';
      const rawProgress = row.progress ?? (loading ? server.load_progress : null);
      const progressPct = loading
        ? Math.min(100, Math.max(0, Number(rawProgress ?? 0)))
        : rawProgress != null
          ? Math.min(100, Math.max(0, Number(rawProgress)))
          : null;
      const progressKnown = loading && rawProgress != null;
      const isEmbedding = server.engine_mode === 'embedding'
        || server.model_kind === 'embedding'
        || row.model_kind === 'embedding'
        || EMBEDDING_PROFILES.has(server.profile);
      let action = '';
      if (row.ejectable && !ejecting) {
        action = ready
          ? isEmbedding
            ? '<button class="lm-btn ghost small" data-action="stop" title="Stop embedding engine and unload its model">Unload</button>'
            : '<button class="lm-btn ghost small" data-action="eject" title="Unload model">Unload</button>'
          : '<button class="lm-btn ghost small" data-action="cancel-load">Cancel</button>';
      }
      const isSelected = actionKey === selectedLoadedKey;
      const inferenceStats = row?.inference_stats || server?.inference_stats || {};
      const isGenerating = inferenceIsGenerating(inferenceStats);
      const cardClass = `lm-model-card lm-model-card-compact ${ejecting ? 'ejecting' : ready ? 'ready' : 'loading'}${isGenerating ? ' generating' : ''}${isSelected ? ' selected' : ''}${row.external ? ' external-gpu' : ' dflash-model'}`;
      const cardStyle = loading ? ` style="--card-progress:${progressPct}%"` : '';
      const installedBadge = row.external ? '' : '<span class="lm-badge installed">Installed</span>';
      const loadChrome = loading && !isGenerating
        ? `<div class="lm-model-card-load-shell${progressKnown ? '' : ' is-indeterminate'}"${cardStyle} aria-hidden="true">
            <span class="lm-model-card-load-label">Loading<span class="lm-loading-dots"><span>.</span><span>.</span><span>.</span></span></span>
            <div class="lm-model-card-load-track"><div class="lm-model-card-load-fill"></div></div>
          </div>`
        : '';
      const ejectChrome = ejecting
        ? `<div class="lm-model-card-eject-shell" aria-hidden="true">
            <span class="lm-model-card-eject-label">Unloading<span class="lm-loading-dots"><span>.</span><span>.</span><span>.</span></span></span>
          </div>`
        : '';
      const badge = ejecting
        ? ''
        : ready
          ? '<span class="lm-badge ready">READY</span>'
          : `<span class="lm-badge loading">${progressPct != null ? `${Math.round(progressPct)}%` : '…'}</span>`;
      const missing = row.path_missing ? '<span class="lm-tag yellow">missing</span>' : '';
      const hoverTitle = cardHoverTitle({ server, row });
      const centerBlock = cardCenterBlock({
        server,
        row,
        ready,
        loading: loading && !isGenerating,
        isGenerating,
        installedBadge,
        statusBadge: badge,
      });

      return `
        <article class="${cardClass}" data-server-id="${escapeHtml(server.id)}" data-role="${escapeHtml(row.role || 'external-gpu')}"${row.external ? ` data-external-pid="${row.pid}"` : ''} role="button" tabindex="0" title="${escapeHtml(hoverTitle)}"${isGenerating ? ' aria-label="Model generating"' : ''}${ejecting ? ' aria-busy="true"' : ''}${cardStyle}>
          ${loadChrome}
          ${ejectChrome}
          <div class="lm-model-card-top">
            ${centerBlock}
            <span class="lm-model-card-tags">${missing}</span>
            <div class="lm-model-stats">
              ${cardTagMetricsHtml(row)}
              ${action}
            </div>
          </div>
        </article>`;
    }).join('');

    wrap.querySelectorAll('[data-action="eject"]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const card = e.target.closest('[data-server-id]');
        const pid = card?.getAttribute('data-external-pid');
        if (pid) {
          void ejectExternalLoad(Number(pid));
          return;
        }
        const serverId = card?.getAttribute('data-server-id');
        if (serverId) void ejectServer(serverId);
      });
    });
    wrap.querySelectorAll('[data-action="cancel-load"]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const card = e.target.closest('[data-server-id]');
        const serverId = card?.getAttribute('data-server-id');
        if (serverId) void stopServer(serverId);
      });
    });
    wrap.querySelectorAll('[data-action="stop"]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const card = e.target.closest('[data-server-id]');
        const serverId = card?.getAttribute('data-server-id');
        if (serverId) void stopServer(serverId);
      });
    });

    wrap.querySelectorAll('.lm-model-card').forEach((card) => {
      const activate = (event) => {
        if (event.type === 'keydown' && event.key !== 'Enter' && event.key !== ' ') return;
        if (event.type === 'keydown') event.preventDefault();
        if (event.target.closest('[data-action]')) return;
        const entry = entryForCard(card);
        if (entry) void selectLoadedCard(entry.server, entry.row);
      };
      card.addEventListener('click', activate);
      card.addEventListener('keydown', activate);
      card.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        const entry = entryForCard(card);
        if (entry) openCardContextMenu(event, entry.server, entry.row);
      });
    });
    syncEngineCardsSectionLabel();
  }

  function modelCatalogKey(model) {
    return model?.server_id || model?.path || model?.id || '';
  }

  function modelGroupId(model) {
    return window.DFlashModelGroups?.groupIdFor?.(model) || 'llm';
  }

  function groupedCatalogModels(list) {
    if (window.DFlashModelGroups?.groupCatalogModels) {
      return window.DFlashModelGroups.groupCatalogModels(list, { catalogKey: modelCatalogKey }).buckets;
    }
    return { llm: list };
  }

  function modelOptionLabel(model) {
    const parts = [model.label || model.filename || model.id || 'Model'];
    if (model.quant && model.quant !== '—') parts.push(model.quant);
    if (model.size_gb != null) parts.push(`${model.size_gb} GB`);
    if (model.loadable && model.port) parts.push(`port :${model.port}`);
    return parts.join(' · ');
  }

  function loadPlanKeyFor(model) {
    if (!model) return '';
    const serverId = model.server_id || activeServer()?.id || '';
    if (!serverId) return '';
    return [
      serverId,
      model.path || model.model_path || model.id || '',
      model.context_size || activeServer()?.context_size || '',
    ].join('|');
  }

  function renderLoadPlanNotice(model) {
    const notice = document.getElementById('serverLoadMemoryNotice');
    const loadBtn = document.getElementById('serverModelLoadBtn');
    if (!notice) return;
    const key = loadPlanKeyFor(model);
    const checking = !!key && loadPlanRequestKey === key;
    const plan = key && currentLoadPlanKey === key ? currentLoadPlan : null;
    notice.classList.add('hidden');
    notice.classList.remove('is-block', 'is-checking');
    if (loadBtn && model) {
      loadBtn.disabled = !canLoadModel(model) || checking || plan?.level === 'block';
    }
    if (!model || !key) return;
    if (checking || !plan) {
      notice.textContent = 'Checking whether this model fits the selected GPU…';
      notice.classList.remove('hidden');
      notice.classList.add('is-checking');
      return;
    }
    if (plan.level === 'ok') return;
    notice.textContent = plan.message || 'GPU memory may be insufficient for this model.';
    notice.classList.remove('hidden');
    if (plan.level === 'block') notice.classList.add('is-block');
  }

  async function refreshLoadPlan(model) {
    const key = loadPlanKeyFor(model);
    if (!key) {
      currentLoadPlan = null;
      currentLoadPlanKey = '';
      loadPlanRequestKey = '';
      renderLoadPlanNotice(null);
      return;
    }
    if (currentLoadPlanKey === key && (currentLoadPlan || loadPlanRequestKey === key)) {
      renderLoadPlanNotice(model);
      return;
    }
    currentLoadPlan = null;
    currentLoadPlanKey = key;
    loadPlanRequestKey = key;
    renderLoadPlanNotice(model);
    const serverId = model.server_id || activeServer()?.id || '';
    const params = new URLSearchParams();
    if (model.path || model.model_path) params.set('model_path', model.path || model.model_path);
    if (model.id) params.set('model_id', model.id);
    try {
      const result = await api(
        `/api/servers/${encodeURIComponent(serverId)}/load-plan?${params.toString()}`,
        { timeoutMs: 30000 },
      );
      if (currentLoadPlanKey !== key) return;
      currentLoadPlan = result;
    } catch {
      if (currentLoadPlanKey !== key) return;
      currentLoadPlan = {
        level: 'warn',
        message: 'GPU fit could not be checked. Loading may fail if the model exceeds available VRAM.',
      };
    } finally {
      if (loadPlanRequestKey === key) loadPlanRequestKey = '';
      if (currentLoadPlanKey === key) renderLoadPlanNotice(model);
    }
  }

  function renderEngineModelPicker() {
    const pick = document.getElementById('serverModelPick');
    const sourcePick = document.getElementById('serverSourcePick');
    const loadBtn = document.getElementById('serverModelLoadBtn');
    if (!pick) return;

    const source = sourcePick?.value || '';
    const sourceKey = String(source).trim().toLowerCase();
    const visibleModels = source
      ? catalogModels.filter((m) => String(window.DFlashModelGroups?.sourceIdFor?.(m) || '').trim().toLowerCase() === sourceKey)
      : catalogModels;
    if (sourcePick && window.DFlashModelGroups?.sourceOptions) {
      sourcePick.innerHTML = ['<option value="">All sources</option>',
        ...window.DFlashModelGroups.sourceOptions(catalogModels).map(([id, label]) =>
          `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`)].join('');
      sourcePick.value = source;
      sourcePick.disabled = false;
      sourcePick.classList.remove('is-loading');
    }
    const placeholder = ENGINE_MODEL_PLACEHOLDER;
    if (window.DFlashModelGroups?.renderGroupedSelectOptions) {
      pick.innerHTML = window.DFlashModelGroups.renderGroupedSelectOptions(visibleModels, {
        catalogKey: modelCatalogKey,
        optionLabel: modelOptionLabel,
        placeholder,
        selectedKey: selectedModelKey,
      });
    } else {
      const buckets = groupedCatalogModels(visibleModels);
      const parts = [`<option value="">${escapeHtml(placeholder)}</option>`];
      for (const group of MODEL_GROUPS) {
        const rows = buckets[group.id] || [];
        if (!rows.length) continue;
        parts.push(`<optgroup label="${escapeHtml(group.label)}">`);
        for (const model of rows) {
          const key = modelCatalogKey(model);
          const selected = key === selectedModelKey ? ' selected' : '';
          parts.push(`<option value="${escapeHtml(key)}"${selected}>${escapeHtml(modelOptionLabel(model))}</option>`);
        }
        parts.push('</optgroup>');
      }
      pick.innerHTML = parts.join('');
    }
    pick.disabled = false;
    pick.classList.remove('is-loading');
    window.DFlashSelectTheme?.syncSelect?.(pick);

    const selected = catalogModels.find((m) => modelCatalogKey(m) === pick.value);
    if (loadBtn) loadBtn.disabled = !canLoadModel(selected);
    renderLoadPlanNotice(selected);
    if (selected) void refreshLoadPlan(selected);
  }

  function resetEngineModelPicker() {
    selectedModelKey = '';
    localStorage.removeItem('dflashConsole.selectedModelKey');
    renderEngineModelPicker();
  }

  function syncModelPicker(key) {
    selectedModelKey = key || '';
    renderEngineModelPicker();
  }

  function selectedCatalogModel() {
    const pick = document.getElementById('serverModelPick');
    if (!pick?.value) return null;
    return catalogModels.find((m) => modelCatalogKey(m) === pick.value) || null;
  }

  async function onEngineModelPickChange() {
    const pick = document.getElementById('serverModelPick');
    const loadBtn = document.getElementById('serverModelLoadBtn');
    const model = selectedCatalogModel();
    selectedModelKey = pick?.value || '';
    if (loadBtn) loadBtn.disabled = !canLoadModel(model);
    if (model?.server_id) {
      activeId = model.server_id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
    if (model) {
      await applyModelSelection(model);
      await window.DFlashModelsLive?.selectModel?.(selectedModelKey, { applyInspector: false });
      await refreshLoadPlan(model);
    } else {
      await refreshLoadPlan(null);
    }
  }

  function setRunningToggle(checked) {
    const toggle = document.getElementById('serverRunningToggle');
    if (!toggle || toggle.checked === checked) return;
    suppressRunningToggle = true;
    toggle.checked = checked;
    suppressRunningToggle = false;
  }

  async function loadPickedModel() {
    const model = selectedCatalogModel();
    if (!canLoadModel(model)) {
      if (model?.path) toast('Pick an engine profile first (use the toolbar toggle), then Load.', false);
      else toast('This model is browse-only — wire it to an engine profile in Settings.', false);
      return;
    }
    if (currentLoadPlanKey === loadPlanKeyFor(model) && currentLoadPlan?.level === 'block') {
      toast(currentLoadPlan.message || 'This model does not fit the current GPU memory.', false);
      return;
    }
    if (window.DFlashModelsLive?.loadModel) {
      await window.DFlashModelsLive.loadModel(model);
      return;
    }
    await loadSelectedModel(model);
  }

  function renderToolbar(server) {
    const statusText = document.getElementById('serverStatusText');
    const toggle = document.getElementById('serverRunningToggle');
    const urlEl = document.getElementById('serverReachableUrl');
    const loadBtn = document.getElementById('serverModelLoadBtn');

    renderEngineModelPicker();

    if (!server) {
      if (statusText) { statusText.textContent = 'No server'; statusText.className = 'lm-status-stopped'; }
      if (toggle) setRunningToggle(false);
      if (urlEl) urlEl.textContent = '—';
      if (loadBtn) loadBtn.disabled = true;
      return;
    }

    const running = serverIsLive(server);
    const label = aggregateStatusLabel();

    if (statusText) {
      statusText.textContent = label;
      const anyActive = loadedServerCount() > 0
        || bootingServerCount() > 0
        || server.running
        || server.status === 'booting';
      statusText.className = anyActive ? 'lm-status-running' : 'lm-status-stopped';
    }
    if (toggle) setRunningToggle(running && getServerAction(server.id) !== 'stopping');
    if (urlEl) urlEl.textContent = server.reachable_url || '—';
    const picked = selectedCatalogModel();
    renderLoadPlanNotice(picked);
    syncEngineCardsSectionLabel();
  }

  function visibleLogLines() {
    const format = window.DFlashLogFormat;
    if (format?.getDisplayLines) return format.getDisplayLines(logLinesRaw, logFilterId);
    return logLinesRaw.slice();
  }

  function updateLogsCount(visibleCount) {
    const countEl = document.getElementById('serverLogsCount');
    if (!countEl) return;
    const total = logLinesRaw.length;
    const visible = typeof visibleCount === 'number' ? visibleCount : visibleLogLines().length;
    if (!total) {
      countEl.textContent = '';
      return;
    }
    if (logFilterId === 'all') {
      countEl.textContent = `${total} lines`;
      return;
    }
    countEl.textContent = `${visible} / ${total}`;
  }

  function renderLogs(lines) {
    logLinesRaw = Array.isArray(lines) ? lines : [];
    const box = document.getElementById('serverLogsBody');
    if (!box) return;
    bindLogsAutoScroll();
    const format = window.DFlashLogFormat?.highlightLogLine;
    const filterLabel = window.DFlashLogFormat?.filterLabel?.(logFilterId) || 'All lines';
    const displayLines = visibleLogLines();
    const stickToBottom = logsFollowTail;
    const savedScroll = stickToBottom
      ? null
      : { top: box.scrollTop, ratio: box.scrollHeight > 0 ? box.scrollTop / box.scrollHeight : 0 };
    if (!logLinesRaw.length) {
      box.innerHTML = '<div class="log-line log-empty"><span class="log-datetime">—</span> <span class="log-dim">No log output yet. Start the engine to capture logs.</span></div>';
      updateLogsCount(0);
      if (stickToBottom) box.scrollTop = box.scrollHeight;
      ensureLogsJumpSentinel();
      window.requestAnimationFrame(updateLogsJumpButton);
      return;
    }
    if (!displayLines.length) {
      box.innerHTML = `<div class="log-line log-empty"><span class="log-dim">No lines match filter “${escapeHtml(filterLabel)}”.</span></div>`;
      updateLogsCount(0);
      if (stickToBottom) box.scrollTop = box.scrollHeight;
      else if (savedScroll) box.scrollTop = Math.max(0, savedScroll.ratio * box.scrollHeight);
      ensureLogsJumpSentinel();
      window.requestAnimationFrame(updateLogsJumpButton);
      return;
    }
    box.innerHTML = displayLines.map((line) => (
      format ? format(line) : `<div class="log-line">${escapeHtml(line)}</div>`
    )).join('');
    updateLogsCount(displayLines.length);
    if (stickToBottom) box.scrollTop = box.scrollHeight;
    else if (savedScroll) box.scrollTop = Math.max(0, savedScroll.ratio * box.scrollHeight);
    ensureLogsJumpSentinel();
    window.requestAnimationFrame(updateLogsJumpButton);
  }

  async function copyVisibleLogs() {
    const lines = visibleLogLines();
    if (!lines.length) {
      toast('Nothing to copy for this filter', false);
      return;
    }
    const text = lines.join('\n');
    try {
      await navigator.clipboard.writeText(text);
      const filterLabel = window.DFlashLogFormat?.filterLabel?.(logFilterId) || 'All lines';
      const suffix = logFilterId === 'all'
        ? `${lines.length} lines`
        : `${lines.length} lines (${filterLabel.toLowerCase()})`;
      toast(`Copied ${suffix}`);
    } catch (error) {
      toast(error.message || 'Copy failed', false);
    }
  }

  function refreshInspectorRecommendations(server) {
    if (!server || inspectorDirty) return;
    const model = {
      server_id: server.id || inspectorBound?.serverId || '',
      profile: server.profile || inspectorBound?.profile,
      size_gb: server.size_gb,
      context_max: PROFILE_CTX_MAX[server.profile] || server.context_max || 262144,
      gpu_layers_max: server.gpu_layers_max || 128,
    };
    window.DFlashRuntimeRecommendations?.scheduleRefresh?.(model);
  }

  function readInspectorLoadSettings() {
    return {
      context_size: parseInt(document.getElementById('inspectorContext')?.value || '65536', 10),
      load_settings: {
        gpu_layers: parseInt(document.getElementById('inspectorGpuLayers')?.value || '99', 10),
        cpu_threads: parseInt(document.getElementById('inspectorCpuThreads')?.value || '9', 10),
        eval_batch_size: parseInt(document.getElementById('inspectorEvalBatch')?.value || '2048', 10),
        physical_batch_size: parseInt(document.getElementById('inspectorPhysicalBatch')?.value || '512', 10),
        flash_attention: !!document.getElementById('inspectorFlashAttention')?.checked,
      },
      inference_settings: {
        temperature: parseFloat(document.getElementById('inspectorTemperature')?.value || '0.7'),
        top_p: parseFloat(document.getElementById('inspectorTopP')?.value || '0.9'),
        top_k: parseInt(document.getElementById('inspectorTopK')?.value || '40', 10),
        repeat_penalty: parseFloat(document.getElementById('inspectorRepeatPenalty')?.value || '1.1'),
        max_tokens: parseInt(document.getElementById('inspectorMaxTokens')?.value || '4096', 10),
      },
    };
  }

  function fillInspectorLoadSettings(server) {
    if (!server || inspectorDirty) return;
    inspectorFilling = true;
    try {
    const load = server.load_settings || {};
    const ctxMax = PROFILE_CTX_MAX[server.profile] || server.context_max || 262144;
    const gpuMax = server.gpu_layers_max || 128;
    const ctxEl = document.getElementById('inspectorContext');
    if (ctxEl) ctxEl.value = server.context_size || 65536;
    if (ctxEl) ctxEl.max = String(ctxMax);

    const gpuEl = document.getElementById('inspectorGpuLayers');
    const gpuLayers = load.gpu_layers ?? 99;
    if (gpuEl) {
      gpuEl.max = String(gpuMax);
      gpuEl.value = gpuLayers;
    }

    document.getElementById('inspectorCpuThreads').value = load.cpu_threads ?? 9;
    document.getElementById('inspectorEvalBatch').value = load.eval_batch_size ?? 2048;
    document.getElementById('inspectorPhysicalBatch').value = load.physical_batch_size ?? 512;
    document.getElementById('inspectorFlashAttention').checked = load.flash_attention !== false;

    const infer = server.inference_settings || {};
    const temperature = infer.temperature ?? 0.7;
    const topP = infer.top_p ?? 0.9;
    const topK = infer.top_k ?? 40;
    const repeatPenalty = infer.repeat_penalty ?? 1.1;
    const tempEl = document.getElementById('inspectorTemperature');
    if (tempEl) tempEl.value = Number(temperature).toFixed(2);
    const topPEl = document.getElementById('inspectorTopP');
    if (topPEl) topPEl.value = Number(topP).toFixed(2);
    const topKEl = document.getElementById('inspectorTopK');
    if (topKEl) topKEl.value = topK;
    const repeatEl = document.getElementById('inspectorRepeatPenalty');
    if (repeatEl) repeatEl.value = Number(repeatPenalty).toFixed(2);
    const maxTokensEl = document.getElementById('inspectorMaxTokens');
    if (maxTokensEl) maxTokensEl.value = infer.max_tokens ?? 4096;

    const specGroup = document.getElementById('inspectorSpeculativeGroup');
    const specHint = document.getElementById('inspectorSpeculativeHint');
    const isEmbedding = EMBEDDING_PROFILES.has(server.profile) || server.engine_mode === 'embedding' || server.model_kind === 'embedding';
    const samplingBlock = document.getElementById('inspectorSamplingGroup');
    if (samplingBlock) samplingBlock.classList.toggle('hidden', isEmbedding);
    if (specGroup) specGroup.classList.toggle('hidden', isEmbedding || !SPEC_PROFILES.has(server.profile));
    if (specHint) {
      if (isEmbedding) {
        const embed = server.embedding_settings || {};
        specHint.textContent = `Embedding engine · ${embed.parameters || '137M'} · ${embed.embedding_dimensions || embed.dimensions || 768} dims · ${server.pooling || embed.pooling || 'mean'} pooling · GPU layers ${gpuLayers}`;
      } else if (server.profile === 'gemma-chat' || server.profile === 'qwen-dflash' || server.profile === 'gemma-12-dflash') {
        specHint.textContent = 'Fixed by profile: draft-dflash speculative decoding.';
      } else if (server.profile === 'gemma-12-ar') {
        specHint.textContent = 'Autoregressive only (no draft).';
      } else if (server.profile === 'bonsai-spec') {
        specHint.textContent = 'Fixed by profile: draft-dspark speculative decoding.';
      } else if (server.profile) {
        specHint.textContent = 'No speculative draft for this profile.';
      }
    }
    refreshInspectorRecommendations({ ...server, id: server.id || inspectorBound?.serverId });
    } finally {
      inspectorFilling = false;
    }
  }

  function fillInspectorInfo(model) {
    if (!model) return;
    document.getElementById('inspectorInfoArch').textContent = model.arch || '—';
    document.getElementById('inspectorInfoParams').textContent = model.params || '—';
    document.getElementById('inspectorInfoQuant').textContent = model.quant || '—';
    document.getElementById('inspectorInfoSize').textContent = model.size_gb != null ? `${model.size_gb} GB` : '—';
    document.getElementById('inspectorInfoContext').textContent = model.context_max ? `${model.context_max} tokens` : '—';
    document.getElementById('inspectorInfoPath').textContent = model.path || model.id || '—';
    document.getElementById('inspectorInfoProfile').textContent = model.profile || (model.external ? 'External' : '—');

    const embedRow = document.getElementById('inspectorInfoEmbeddingRow');
    const embedEl = document.getElementById('inspectorInfoEmbedding');
    const embed = model.embedding_settings || {};
    const embedText = (model.model_kind === 'embedding' || EMBEDDING_PROFILES.has(model.profile))
      ? [
          embed.model_family || 'nomic-embed-text',
          embed.model_version || 'v1.5',
          embed.parameters || '137M',
          `${embed.embedding_dimensions || embed.dimensions || 768} dims`,
          `${model.pooling || embed.pooling || 'mean'} pooling`,
          embed.api_path || '/v1/embeddings',
        ].filter(Boolean).join(' · ')
      : '';
    if (embedRow && embedEl) {
      embedRow.classList.toggle('hidden', !embedText);
      embedEl.textContent = embedText || '—';
    }

    const vramRow = document.getElementById('inspectorInfoVramRow');
    const vramEl = document.getElementById('inspectorInfoVram');
    const vramText = formatCardGb(model.vram_gb, { vram: true });
    if (vramRow && vramEl) {
      vramRow.classList.toggle('hidden', !vramText);
      vramEl.textContent = vramText || '—';
    }

    const gpuRow = document.getElementById('inspectorInfoGpuRow');
    const gpuEl = document.getElementById('inspectorInfoGpu');
    if (gpuRow && gpuEl) {
      gpuRow.classList.toggle('hidden', !model.gpu_display);
      gpuEl.textContent = model.gpu_display || '—';
    }

    const portRow = document.getElementById('inspectorInfoPortRow');
    const portEl = document.getElementById('inspectorInfoPort');
    const port = model.listen_port || model.port;
    if (portRow && portEl) {
      portRow.classList.toggle('hidden', !port);
      portEl.textContent = port ? `:${port}` : '—';
    }

    const appRow = document.getElementById('inspectorInfoAppRow');
    const appEl = document.getElementById('inspectorInfoApp');
    if (appRow && appEl) {
      appRow.classList.toggle('hidden', !model.external);
      appEl.textContent = model.app_label || '—';
    }

    const caps = document.getElementById('inspectorInfoCaps');
    if (caps) {
      const tags = [];
      const list = model.capabilities || [];
      if (list.includes('tools')) tags.push('<span class="lm-tag green">tools</span>');
      if (list.includes('ar')) tags.push('<span class="lm-tag blue">AR</span>');
      if (list.includes('dflash')) tags.push(dflashLogoLabel());
      list.forEach((cap) => {
        if (cap === 'instruct' || cap === 'tools' || cap === 'ar' || cap === 'dflash') return;
        tags.push(`<span class="lm-tag blue">${escapeHtml(cap)}</span>`);
      });
      caps.innerHTML = tags.join('') || '—';
    }
    const draftRow = document.getElementById('inspectorInfoDraftRow');
    const draftEl = document.getElementById('inspectorInfoDraft');
    if (draftRow && draftEl) {
      const hasDraft = !!model.draft_label;
      draftRow.classList.toggle('hidden', !hasDraft);
      draftEl.textContent = hasDraft ? model.draft_label : '—';
    }
    document.getElementById('inspectorHeadTitle')?.replaceChildren(document.createTextNode(inspectorModelTitle(model)));
  }

  async function applyModelSelection(model) {
    if (!model) return;
    await flushInspectorSave();
    inspectorDirty = false;
    inspectorPendingReload = false;
    inspectorBound = {
      serverId: model.server_id || '',
      modelKey: modelKeyFor(model),
      profile: model.profile,
      context_max: model.context_max,
      gpu_layers_max: model.gpu_layers_max,
      external: !!model.external,
    };
    fillInspectorInfo(model);
    if (!model.external) {
      fillInspectorLoadSettings(getMergedLoadSettings(model));
    }
    syncInspectorLoadedState(model);
    if (model.server_id) {
      activeId = model.server_id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
  }

  async function saveInspectorLoadSettings() {
    await flushInspectorSave();
  }

  function fillSettingsForm(server) {
    if (!server) return;
    const pick = document.getElementById('serverSettingsPick');
    if (pick) {
      pick.innerHTML = allServers.map((s) =>
        `<option value="${escapeHtml(s.id)}"${s.id === activeId ? ' selected' : ''}>${escapeHtml(s.label || s.id)}</option>`,
      ).join('');
    }
    document.getElementById('serverSettingsPort').value = server.port;
    document.getElementById('serverSettingsHost').value = server.host;
    document.getElementById('serverSettingsContext').value = server.context_size;
    document.getElementById('serverSettingsIdle').value = server.idle_unload_minutes;
    document.getElementById('serverSettingsProfile').value = server.profile;
    const gpuSel = document.getElementById('serverSettingsGpu');
    if (gpuSel) {
      gpuSel.innerHTML = '<option value="auto">Automatic</option>' + gpus.map((g) =>
        `<option value="${g.index}"${String(server.gpu_device) === String(g.index) ? ' selected' : ''}>${escapeHtml(g.display_name || g.name)}</option>`,
      ).join('');
    }
    fillInspectorLoadSettings(server);
  }

  function renderAll() {
    syncPendingLoadsFeed();
    const server = activeServer();
    renderToolbar(server);
    renderCards();
    if (!server) return;

    if (inspectorBound?.external) {
      const pid = Number(String(inspectorBound.modelKey || '').replace(/^external-/, ''));
      const row = externalGpuLoads.find((entry) => Number(entry.pid) === pid);
      if (row) {
        const extServer = {
          id: `external-${row.pid}`,
          label: row.app_label || 'External app',
          port: row.listen_port || '',
          external: true,
          status: 'loaded',
          gpu_display: row.gpu_display || '',
        };
        const model = modelFromLoadedEntry(extServer, row);
        fillInspectorInfo(model);
        syncInspectorLoadedState(model);
      }
      return;
    }

    if (inspectorBound?.serverId === server.id) {
      if (!inspectorDirty && !inspectorPendingReload && !inspectorFilling) {
        fillInspectorLoadSettings(server);
      }
      const row = loadedRowsForServer(server)[0] || {};
      const boundModel = modelFromLoadedEntry(server, row);
      fillInspectorInfo({ ...boundModel, server_id: server.id, loaded_on_gpu: server.status === 'loaded' });
      syncInspectorLoadedState({ ...boundModel, server_id: server.id, loaded_on_gpu: server.status === 'loaded' });
      return;
    }

    if (document.body.dataset.activeView !== 'server') return;

    const model = modelFromLoadedEntry(server, loadedRowsForServer(server)[0] || {});
    fillInspectorInfo(model);
    if (!inspectorDirty && !inspectorPendingReload && !inspectorFilling) {
      fillInspectorLoadSettings(getMergedLoadSettings(model));
    }
    inspectorBound = {
      serverId: server.id,
      modelKey: modelKeyFor(model),
      profile: model.profile,
      context_max: model.context_max,
      gpu_layers_max: model.gpu_layers_max,
    };
  }

  async function clearLogs() {
    const server = activeServer();
    if (!server) return;
    try {
      await api(`/api/logs/${encodeURIComponent(server.id)}`, { method: 'DELETE' });
      renderLogs([]);
      logsFollowTail = true;
      toast('Engine log cleared');
    } catch (err) {
      toast(err.message, false);
    }
  }

  async function refreshLogs() {
    const server = activeServer();
    if (!server) return;
    if (server.id !== lastLogsServerId) {
      lastLogsServerId = server.id;
      logsFollowTail = true;
    }
    const data = await api(`/api/logs/${encodeURIComponent(server.id)}?tail=${LOG_FETCH_TAIL}`);
    renderLogs(data.lines || []);
  }

  let catalogRefreshGen = 0;
  let catalogLoaded = false;

  function catalogSignature(models) {
    return (models || []).map((m) => modelCatalogKey(m)).join('\n');
  }

  async function refreshCatalog({ force = false, shouldRender = true } = {}) {
    const gen = ++catalogRefreshGen;
    const modelPick = document.getElementById('serverModelPick');
    const settingsPick = document.getElementById('serverSettingsPick');
    const showLoading = force || !catalogLoaded;

    statusFetchPending = true;
    updateEnginePageNotice();

    if (shouldRender && showLoading) {
      setSelectLoading(modelPick, true, 'Loading models…');
      if (settingsPick && !settingsPick.options.length) {
        setSelectLoading(settingsPick, true, 'Loading engines…');
      }
    }

    // Start the GPU scan in parallel with profile/model discovery so
    // external apps such as OneVoice Whisper appear without waiting for the
    // slower DFlash catalog/status requests to finish.
    void refreshExternalGpuLoads(shouldRender);
    void captureConsoleBoot();

    try {
      const [profilesData, quickModelsData] = await Promise.all([
        api('/api/servers/profiles'),
        api('/api/models?quick=1'),
      ]);
      if (gen !== catalogRefreshGen) return;
      allServers = profilesData.all_servers || profilesData.servers || [];
      servers = profilesData.servers || [];
      catalogModels = quickModelsData.models || [];
      catalogLoaded = true;
      if (!activeId || !allServers.some((s) => s.id === activeId)) {
        activeId = profilesData.primary_server_id || servers[0]?.id || allServers[0]?.id || '';
        localStorage.setItem('dflashConsole.activeServerId', activeId);
      }
      if (shouldRender) {
        renderAll();
        if (showLoading) {
          setSelectLoading(modelPick, false);
          setSelectLoading(settingsPick, false);
        }
      }
    } catch (err) {
      if (gen !== catalogRefreshGen) return;
      if (shouldRender && showLoading) {
        setSelectLoading(modelPick, false);
        setSelectLoading(settingsPick, false);
        toast(err.message, false);
      }
    }

    try {
      const [data, modelsData] = await Promise.all([
        api(serversStatusUrl(false), { timeoutMs: 15000 }),
        api('/api/models', { timeoutMs: 30000 }).catch(() => ({ models: [] })),
      ]);
      if (gen !== catalogRefreshGen) return;
      if (!applyServersPayload(data, { mergeExternal: false })) return;
      initialStatusSettled = true;
      const nextModels = modelsData.models || [];
      const modelsChanged = catalogSignature(nextModels) !== catalogSignature(catalogModels);
      catalogModels = nextModels;
      catalogLoaded = true;
      if (!activeId || !allServers.some((s) => s.id === activeId)) {
        activeId = data.primary_server_id || servers[0]?.id || allServers[0]?.id || '';
        localStorage.setItem('dflashConsole.activeServerId', activeId);
      }
      syncActiveIdFromLiveState();
      if (shouldRender) {
        if (modelsChanged || showLoading) renderAll();
        else renderToolbar(activeServer());
        await refreshLogs();
      }
      window.DFlashStatusFeed?.refresh?.();
    } catch {
      if (gen !== catalogRefreshGen) return;
      // Profiles already loaded — do not keep the Engines page on a forever spinner.
      if (catalogLoaded) {
        initialStatusSettled = true;
        if (shouldRender) renderAll();
      }
    } finally {
      if (gen === catalogRefreshGen) {
        statusFetchPending = false;
        updateEnginePageNotice();
      }
    }

    void refreshExternalGpuLoads(shouldRender).finally(() => {
      if (shouldRender) updateEnginePageNotice();
    });
  }

  async function refreshStatus(
    shouldRender = true,
    { includeExternal = false, fresh = false } = {},
  ) {
    try {
      const data = await api(serversStatusUrl(includeExternal, fresh), { timeoutMs: fresh ? 30000 : 15000 });
      if (!applyServersPayload(data, { mergeExternal: includeExternal })) return;
      initialStatusSettled = true;
      syncActiveIdFromLiveState();
      if (shouldRender) {
        renderAll();
        updateEnginePageNotice();
        await refreshLogs();
      }
    } catch {
      /* keep last known state */
    }
  }

  async function refresh(shouldRender = true, { fresh = false } = {}) {
    if (!catalogLoaded) {
      await refreshCatalog({ shouldRender });
    } else {
      await refreshStatus(shouldRender, { fresh });
    }
    reschedulePoll();
  }

  async function refreshAfterUnload() {
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    await refreshStatus(true, { includeExternal: true, fresh: true });
    reschedulePoll();
  }

  async function pollTick() {
    if (pollInFlight) return;
    pollInFlight = true;
    const view = document.body.dataset.activeView;
    externalPollCounter += 1;
    try {
      await refresh(view === 'server');
    } finally {
      pollInFlight = false;
    }
    if (view === 'server' && (externalPollCounter === 1 || externalPollCounter % 3 === 0)) {
      void refreshExternalGpuLoads(true);
    }
    if (view === 'models' && window.DFlashModelsLive) {
      try {
        await window.DFlashModelsLive.refresh();
      } catch {
        /* ignore */
      }
    }
  }

  async function waitUntilModelLoaded(serverId, { maxAttempts = 180 } = {}) {
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await refresh(true);
      const server = servers.find((s) => s.id === serverId);
      if (server?.status === 'loaded') return server;
      if (server?.status === 'error') return server;
      if (server?.load_error || server?.boot_error) return server;
      if (
        server
        && !server.loaded_models?.length
        && !server.booting
        && server.status !== 'booting'
        && server.status !== 'running'
        && attempt > 2
      ) {
        return server;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    return servers.find((s) => s.id === serverId) || null;
  }

  async function startActive() {
    const server = activeServer();
    if (!server || isServerBusy(server.id)) return;
    if (serverIsLive(server) && server.status !== 'stopped') {
      toast('Engine is already running');
      setRunningToggle(true);
      return;
    }
    setServerAction(server.id, 'starting');
    updateEnginePageNotice();
    window.DFlashStatusFeed?.setTransient(`Starting engine ${server.label || server.id}…`, {
      secondary: `Port :${server.port}`,
      ttlMs: 120000,
    });
    renderAll();
    try {
      await api(`/api/servers/${encodeURIComponent(server.id)}/listen`, { method: 'POST' });
      toast('Engine started');
      window.DFlashStatusFeed?.note('Engine listening', `Port :${server.port} · no model loaded yet`);
      await refresh(true, { fresh: true });
    } catch (err) {
      toast(err.message, false);
    } finally {
      setServerAction(server.id, null);
      renderAll();
    }
  }

  async function waitUntilServerIdle(serverId, maxAttempts = 30) {
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const data = await api('/api/servers');
      servers = data.servers || [];
      allServers = data.all_servers || servers;
      const server = servers.find((s) => s.id === serverId);
      if (server && !server.loaded_models?.length && !server.booting && server.status !== 'booting') {
        return server;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 400));
    }
    return activeServer();
  }

  async function executeModelLoad(model, forceServerId) {
    const serverId = forceServerId || model.server_id || activeServer()?.id;
    if (!serverId) {
      toast('Select an engine first', false);
      return;
    }
    const label = model.label || model.id;
    setServerAction(serverId, 'loading');
    pendingLoads.set(serverId, { label, plain_gguf: !!model.plain_gguf });
    syncPendingLoadsFeed();
    window.DFlashStatusFeed?.setTransient(`Loading ${label}…`, {
      secondary: 'Reading weights into GPU',
      ttlMs: 120000,
    });
    renderAll();
    try {
      await saveInspectorLoadSettings();
      const body = {};
      if (shouldSendModelPath(model, serverId)) {
        body.model_path = model.path;
        if (model.id) body.model_id = model.id;
      }
      const result = await api(`/api/servers/${encodeURIComponent(serverId)}/load`, {
        method: 'POST',
        body: Object.keys(body).length ? JSON.stringify(body) : undefined,
      });
      if (result?.memory_warning) {
        toast(result.memory_warning);
        window.DFlashStatusFeed?.note(result.memory_warning, label);
      }
      if (result?.already_loaded) {
        toast('Model already loaded');
        window.DFlashStatusFeed?.note(`${label} ready`, `Port :${result.port || '—'}`);
        await refresh(true, { fresh: true });
        return;
      }
      const loaded = await waitUntilModelLoaded(serverId);
      if (loaded?.status === 'loaded') {
        toast('Model loaded');
        window.DFlashStatusFeed?.note(`${label} ready`, `Port :${loaded.port || '—'}`);
        clearInspectorPendingReload();
      } else if (loaded?.status === 'error') {
        const message = loaded.boot_error || loaded.load_error || 'Model load failed. Check the engine log.';
        toast(message, false);
        window.DFlashStatusFeed?.note('Load failed', message);
      } else if (loaded && !loaded.loaded_models?.length) {
        const message = loaded.load_error
          || 'Model load did not complete. Check the engine log and try again.';
        toast(message, false);
        window.DFlashStatusFeed?.note('Load did not complete', message);
      }
    } catch (err) {
      toast(err.message, false);
      window.DFlashStatusFeed?.note('Load failed', err.message || label);
    } finally {
      pendingLoads.delete(serverId);
      syncPendingLoadsFeed();
      setServerAction(serverId, null);
      resetEngineModelPicker();
      renderAll();
      await refresh(true, { fresh: true });
    }
  }

  async function loadSelectedModel(model) {
    const serverId = model?.server_id || activeServer()?.id;
    if (!serverId) {
      toast('Select an engine first', false);
      return;
    }
    if (!canLoadModel(model)) {
      if (isServerBusy(serverId)) toast('This engine is already busy', false);
      return;
    }
    await applyModelSelection(model);
    activeId = serverId;
    localStorage.setItem('dflashConsole.activeServerId', activeId);
    ensureInspectorVisible();
    focusInspectorTab('load');
    void executeModelLoad({ ...model, server_id: model.server_id || '' });
  }

  async function ejectExternalLoad(pid) {
    if (!pid || Number.isNaN(pid)) return;
    const key = `external-${pid}`;
    if (isServerBusy(key)) return;
    const row = externalGpuLoads.find((entry) => Number(entry.pid) === Number(pid));
    const label = row?.title || row?.app_label || `PID ${pid}`;
    setServerAction(key, 'ejecting');
    window.DFlashStatusFeed?.setTransient(`Stopping ${label}…`, {
      secondary: row?.app_label ? `External · ${row.app_label}` : 'External GPU process',
      ttlMs: 30000,
    });
    renderAll();
    try {
      const body = {};
      if (row?.api_url) body.api_url = row.api_url;
      if (row?.model_id) body.model_id = row.model_id;
      await api(`/api/gpu/processes/${encodeURIComponent(pid)}/unload`, {
        method: 'POST',
        body: Object.keys(body).length ? JSON.stringify(body) : undefined,
      });
      toast('External model unloaded');
      setServerAction(key, null);
      renderAll();
      await refreshAfterUnload();
    } catch (err) {
      toast(err.message, false);
    } finally {
      setServerAction(key, null);
      renderAll();
    }
  }

  async function ejectServer(serverId) {
    if (!serverId || isServerBusy(serverId)) return;
    setServerAction(serverId, 'ejecting');
    const label = allServers.find((s) => s.id === serverId)?.label || serverId;
    window.DFlashStatusFeed?.setTransient(`Unloading ${label}…`, { ttlMs: 30000 });
    renderAll();
    let unloaded = false;
    try {
      await api(`/api/servers/${encodeURIComponent(serverId)}/unload`, { method: 'POST' });
      unloaded = true;
      toast('Model unloaded');
      await waitUntilServerIdle(serverId);
      activeId = serverId;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
      await refreshLogs();
    } catch (err) {
      toast(err.message, false);
    } finally {
      setServerAction(serverId, null);
      if (inspectorBound?.serverId === serverId) clearInspectorPendingReload();
      renderAll();
      await refreshAfterUnload();
    }
    return unloaded;
  }

  async function ejectActive() {
    const server = activeServer();
    if (!server) return;
    await ejectServer(server.id);
  }

  async function stopServer(serverId) {
    if (!serverId || isServerBusy(serverId)) return;
    setServerAction(serverId, 'stopping');
    const label = allServers.find((s) => s.id === serverId)?.label || serverId;
    window.DFlashStatusFeed?.setTransient(`Stopping ${label}…`, { ttlMs: 30000 });
    renderAll();
    try {
      await api(`/api/servers/${encodeURIComponent(serverId)}/stop`, { method: 'POST' });
      toast('Server stopped');
      pendingLoads.delete(serverId);
      await refresh(true, { fresh: true });
    } catch (err) {
      toast(err.message, false);
    } finally {
      setServerAction(serverId, null);
      renderAll();
    }
  }

  async function stopActive() {
    const server = activeServer();
    if (!server) return;
    await stopServer(server.id);
  }

  async function saveGatewaySettings() {
    const server = activeServer();
    if (!server) return;
    const patch = {
      port: parseInt(document.getElementById('serverSettingsPort').value, 10),
      host: document.getElementById('serverSettingsHost').value.trim(),
      context_size: parseInt(document.getElementById('serverSettingsContext').value, 10),
      idle_unload_minutes: parseInt(document.getElementById('serverSettingsIdle').value, 10),
      gpu_device: document.getElementById('serverSettingsGpu').value,
      profile: document.getElementById('serverSettingsProfile').value,
    };
    await api(`/api/servers/${encodeURIComponent(server.id)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
    window.DFlashStatusFeed?.note('Engine settings saved');
    await refresh();
  }

  async function saveSettings() {
    await saveGatewaySettings();
    toast('Engine settings saved');
  }

  function startPolling() {
    reschedulePoll();
  }

  function bind() {
    window.DFlashRuntimeSteppers?.bindInspectorSteppers?.();

    const autoSaveIds = [
      'inspectorContext', 'inspectorGpuLayers', 'inspectorCpuThreads', 'inspectorEvalBatch',
      'inspectorPhysicalBatch', 'inspectorFlashAttention', 'inspectorTemperature', 'inspectorTopP',
      'inspectorTopK', 'inspectorRepeatPenalty', 'inspectorMaxTokens',
    ];
    autoSaveIds.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      const eventName = el.type === 'checkbox' ? 'change' : 'input';
      el.addEventListener(eventName, scheduleInspectorAutoSave);
      if (el.type === 'number') {
        el.addEventListener('change', scheduleInspectorAutoSave);
      }
    });

    document.getElementById('serverRunningToggle')?.addEventListener('change', (e) => {
      if (suppressRunningToggle) return;
      if (e.target.checked) void startActive();
      else void stopActive();
    });
    document.getElementById('serverModelLoadBtn')?.addEventListener('click', () => void loadPickedModel());
    document.getElementById('inspectorReloadBtn')?.addEventListener('click', () => void reloadInspectorModel());
    document.getElementById('serverModelPick')?.addEventListener('change', () => {
      void onEngineModelPickChange();
    });
    document.getElementById('serverSourcePick')?.addEventListener('change', () => {
      selectedModelKey = '';
      localStorage.removeItem('dflashConsole.selectedModelKey');
      renderEngineModelPicker();
    });
    document.getElementById('serverCopyUrl')?.addEventListener('click', () => {
      const url = document.getElementById('serverReachableUrl')?.textContent;
      if (url && url !== '—') navigator.clipboard.writeText(url).then(() => toast('URL copied'));
    });
    document.getElementById('serverLogsRefresh')?.addEventListener('click', () => void refreshLogs().catch((e) => toast(e.message, false)));
    document.getElementById('serverLogsCopy')?.addEventListener('click', () => void copyVisibleLogs());
    document.getElementById('serverLogsClear')?.addEventListener('click', () => void clearLogs());
    bindLogsFilterDropdown();

    document.getElementById('engineCardsFilterBtn')?.addEventListener('click', () => {
      cycleEngineCardsFilter();
    });

    document.getElementById('serverSettingsPick')?.addEventListener('change', (e) => {
      activeId = e.target.value;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
      fillSettingsForm(allServers.find((s) => s.id === activeId) || activeServer());
      void refresh();
    });

    document.addEventListener('click', hideCardContextMenu);
    document.addEventListener('scroll', hideCardContextMenu, true);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideCardContextMenu();
    });
  }

  async function loadModelOnServer(serverId, model) {
    if (!serverId || !model) {
      toast('Select an engine and model', false);
      return false;
    }
    if (isServerBusy(serverId)) {
      toast('This engine is already busy', false);
      return false;
    }
    const server = allServers.find((s) => s.id === serverId) || servers.find((s) => s.id === serverId);
    if (server?.status === 'booting' || server?.booting) {
      toast('Engine is still booting', false);
      return false;
    }
    const payload = { ...model };
    if (shouldSendModelPath(payload, serverId)) {
      payload.server_id = '';
    } else if (payload.server_id !== serverId) {
      payload.server_id = '';
    }
    if (!payload.server_id && !payload.path) {
      toast('This model cannot be loaded', false);
      return false;
    }
    await executeModelLoad(payload, serverId);
    return true;
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    updateEnginePageNotice();
    void initEngineFilters()
      .then(() => refresh())
      .then(startPolling)
      .catch((err) => toast(err.message, false));
  });

  window.DFlashServerLive = {
    refresh,
    startActive,
    ejectActive,
    stopActive,
    activeServer,
    applyModelSelection,
    loadSelectedModel,
    loadModelOnServer,
    fillSettingsForm,
    saveGatewaySettings,
    fillInspectorLoadSettings,
    flushInspectorSave,
    getMergedLoadSettings,
    modelKeyFor,
    syncModelPicker,
    resetEngineModelPicker,
    rememberInspectorTab,
    focusInspectorTab,
  };
})();
