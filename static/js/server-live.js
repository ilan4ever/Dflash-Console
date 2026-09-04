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
  let totalVramGb = null;
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
  let initialStatusSettled = false;
  let pollInFlight = false;
  let latestStatusRevision = 0;
  let externalFetchPending = false;
  let externalInitialFetchDone = false;
  let externalScanError = '';
  let gpuRescanPending = false;
  let lastStatusTrace = [];
  let lastStatusBuildMs = 0;
  let engineStatusLoadingDetail = '';
  let externalMissingPolls = 0;
  let externalPollCounter = 0;
  let inferenceStatsTimer = null;
  const LIVE_STATS_INTERVAL_MS = 250;

  function enginesViewActive() {
    return document.body.dataset.activeView === 'server';
  }

  function anyExternalLoading() {
    return externalGpuLoads.some((row) => row?.card_state === 'loading');
  }

  function anyExternalGpuBusy() {
    return externalGpuLoads.some((row) => row?.gpu_busy || inferenceIsGenerating(row?.inference_stats));
  }

  function enginesNeedFastRefresh() {
    return anyServerLoading()
      || hasPendingEngineActions()
      || anyServerGenerating()
      || anyExternalLoading();
  }
  // Console OpenAI gateway — one stable OpenAI-compatible base URL shown on the
  // toolbar API badge / copy button instead of the raw per-engine port.
  let gatewayUrl = '';
  let gatewayUrlFetch = null;

  async function loadGatewayUrl({ rerender = false } = {}) {
    if (gatewayUrlFetch) return gatewayUrlFetch;
    gatewayUrlFetch = (async () => {
      try {
        const data = await api('/api/config', { timeoutMs: 8000 });
        const cfg = data?.config || {};
        gatewayUrl = `http://127.0.0.1:${Number(cfg.gateway_port) || 8001}/v1`;
      } catch (_err) {
        gatewayUrl = '';
      } finally {
        gatewayUrlFetch = null;
      }
      if (rerender) {
        const urlEl = document.getElementById('serverReachableUrl');
        if (urlEl) urlEl.textContent = gatewayUrl || '—';
      }
    })();
    return gatewayUrlFetch;
  }

  const MODEL_GROUPS = window.DFlashModelGroups?.GROUPS || [
    { id: 'dflash', label: 'DFlash 1' },
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

  // An external path is importable when it is a .gguf file OR a directory-like
  // path (faster-whisper model directories have no file extension and often
  // live under HF snapshot folders). Files with an obvious extension are not
  // importable through import-into-console.
  function isImportableSttDir(pathValue) {
    const text = String(pathValue || '').trim();
    if (!text) return false;
    const last = String(text.split(/[\\/]/).pop() || '');
    if (/\.gguf$/i.test(text)) return false; // handled by the .gguf branch
    if (/\.[a-z0-9]{1,8}$/i.test(last)) return false; // looks like a file
    return true; // extension-less path → likely a model directory
  }

  function getServerAction(serverId) {
    return serverId ? (serverActions.get(serverId) || null) : null;
  }

  function setServerAction(serverId, action) {
    if (!serverId) return;
    if (action) serverActions.set(serverId, action);
    else serverActions.delete(serverId);
    updateEnginePageNotice();
    reschedulePoll();
  }

  function hasPendingEngineActions() {
    return serverActions.size > 0 || pendingLoads.size > 0;
  }

  function modelPickerRefreshPaused() {
    const pick = document.getElementById('serverModelPick');
    const sourcePick = document.getElementById('serverSourcePick');
    if (window.DFlashSelectTheme?.isMenuOpen?.(pick)) return true;
    if (window.DFlashSelectTheme?.isMenuOpen?.(sourcePick)) return true;
    return hasPendingEngineActions();
  }

  function isServerBusy(serverId) {
    return !!getServerAction(serverId);
  }

  function isAcceleratorOnlyModel(model) {
    if (window.DFlashModelGroups?.isAcceleratorOnlyModel) {
      return window.DFlashModelGroups.isAcceleratorOnlyModel(model);
    }
    if (!model) return false;
    if (model.dflash_stack || model.draft_path) return false;
    if (model.plain_llm || model.is_adhoc || String(model.role || '') === 'loaded-model') return false;
    const size = Number(model.size_gb);
    if (Number.isFinite(size) && size > 8) return false;
    if (model.accelerator_only === true) return true;
    const name = `${model.filename || ''} ${model.path || ''}`.toLowerCase();
    return /\.gguf/i.test(name) && /dflash|dspark|(?:^|[^a-z])draft(?:[^a-z]|$)/.test(name);
  }

  function currentLoadEngine() {
    return window.DFlashModelsLive?.getLoadEngine?.() || 'dflash';
  }

  function canLoadModel(model) {
    if (!model) return false;
    if (isAcceleratorOnlyModel(model)) return false;
    const loadEngine = currentLoadEngine();
    if (loadEngine === 'vllm' || loadEngine === 'transformers' || loadEngine === 'freetoken') {
      return !!model.path;
    }
    const serverId = model.server_id || activeServer()?.id;
    if (!serverId) return false;
    if (isServerBusy(serverId)) return false;
    const server = servers.find((s) => s.id === serverId) || allServers.find((s) => s.id === serverId);
    if (server?.status === 'booting' || server?.booting) return false;
    if (model.loadable && model.server_id) return true;
    return !!model.path;
  }

  function anyServerLoading() {
    return pendingLoads.size > 0 || servers.some((s) => s.status === 'booting' || s.booting || s.warming);
  }

  function normalizeLoadProgress(raw) {
    return window.DFlashLoadProgress?.normalize?.(raw) || {
      pct: Number.isFinite(Number(raw)) ? Number(raw) : null,
      detail: '',
      phase: '',
      eta_seconds: null,
      elapsed_seconds: null,
      expert_present: null,
      expert_total: null,
    };
  }

  function serverIsWarming(server) {
    return window.DFlashLoadProgress?.isWarming?.(server)
      || !!(server?.warming || server?.booting || server?.status === 'booting');
  }

  function hasVisibleLoadingCards() {
    return collectLoadedEntries().some(({ row }) => row?.card_state === 'loading');
  }

  function pendingLoadRow(serverId) {
    const meta = pendingLoads.get(serverId);
    if (!meta) return null;
    const server = servers.find((s) => s.id === serverId);
    const progress = normalizeLoadProgress(server?.load_progress);
    return {
      card_state: 'loading',
      title: meta.label,
      label: meta.label,
      role: 'alias',
      ejectable: true,
      progress: progress.pct ?? server?.load_progress ?? null,
      progress_detail: progress.detail || '',
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
      if (row?.gpu_busy) return true;
    }
    return false;
  }

  function pollIntervalMs() {
    const onEngines = enginesViewActive();
    if (anyServerGenerating() || anyExternalGpuBusy()) return onEngines ? 250 : 500;
    const ejecting = [...serverActions.values()].some((value) => value === 'ejecting');
    if (ejecting) return onEngines ? 250 : 400;
    if (pendingLoads.size > 0 || bootingServerCount() > 0) return onEngines ? 300 : 500;
    if (enginesNeedFastRefresh() || loadedServerCount() > 0) {
      return onEngines ? 700 : 800;
    }
    return onEngines ? 1200 : 2500;
  }

  function loadProgressDisplay(loading, rawProgress, { warming = false } = {}) {
    const norm = normalizeLoadProgress(rawProgress);
    const pct = norm.pct != null ? Math.min(100, Math.max(0, Number(norm.pct))) : null;
    const known = pct != null && pct > 0;
    let etaSeconds = Number(norm.eta_seconds);
    if (
      !Number.isFinite(etaSeconds)
      && Number(norm.expert_present) > 0
      && Number(norm.expert_total) > Number(norm.expert_present)
      && Number(norm.elapsed_seconds) > 0
    ) {
      etaSeconds = (
        (Number(norm.expert_total) - Number(norm.expert_present))
        * Number(norm.elapsed_seconds)
        / Number(norm.expert_present)
      );
    }
    const etaLabel = Number.isFinite(etaSeconds) && etaSeconds >= 0
      ? `~${formatLoadEta(etaSeconds)} remaining`
      : '';
    if (!loading) {
      if (!known) return { pct: null, known: false, label: '', etaLabel, detail: norm.detail, phase: norm.phase };
      return { pct, known: true, label: `${Math.round(pct)}%`, etaLabel, detail: norm.detail, phase: norm.phase };
    }
    if (known) {
      const prefix = warming ? 'Warming' : 'Loading';
      return {
        pct,
        known: true,
        label: `${prefix} ${Math.round(pct)}%`,
        etaLabel,
        detail: norm.detail,
        phase: norm.phase,
      };
    }
    const fallback = warming ? 'Warming' : (norm.detail || 'Loading');
    return {
      pct: null,
      known: false,
      label: fallback,
      etaLabel,
      detail: norm.detail,
      phase: norm.phase,
    };
  }

  function formatLoadEta(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total < 60) return `${total}s`;
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
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

  function formatStatusTraceDetail() {
    if (!Array.isArray(lastStatusTrace) || !lastStatusTrace.length) {
      return engineStatusLoadingDetail;
    }
    const lines = lastStatusTrace.map((row) => {
      const detail = String(row?.detail || row?.step || '').trim();
      const ms = Number(row?.ms || 0);
      if (!detail) return '';
      return ms > 0 ? `${detail} (${ms} ms)` : detail;
    }).filter(Boolean);
    if (lastStatusBuildMs > 0) {
      lines.push(`Total ${lastStatusBuildMs} ms`);
    }
    return lines.join(' · ');
  }

  function gpuCardsSectionReady() {
    // Show DFlash cards as soon as /api/servers returns. Do not block the page
    // on the slower external GPU scan — that continues in the background.
    if (pendingLoads.size > 0) return true;
    if (gpuRescanPending) return false;
    return initialStatusSettled;
  }

  function updateEnginePageNotice() {
    const banner = document.getElementById('enginePageNotice');
    const titleEl = document.getElementById('enginePageNoticeTitle');
    const detailEl = document.getElementById('enginePageNoticeDetail');
    if (!banner || !titleEl || !detailEl) return;

    const server = activeServer();
    const action = server ? getServerAction(server.id) : '';
    const startingCount = [...serverActions.values()].filter((value) => value === 'starting').length;

    let title = '';
    let detail = '';
    let mode = 'loading';

    if (!initialStatusSettled) {
      title = 'Loading engine status…';
      detail = formatStatusTraceDetail() || 'Checking llama-server listeners and loaded models.';
      mode = 'loading';
    } else if (externalScanError && !externalGpuLoads.length && externalInitialFetchDone) {
      title = 'Could not scan external GPU models';
      detail = `${externalScanError} Restart the Console API (server.ps1 -ApiRestart), then refresh this page.`;
      mode = 'loading';
    } else if (externalFetchPending && !externalGpuLoads.length && !externalInitialFetchDone) {
      title = 'Scanning external GPU models…';
      detail = formatStatusTraceDetail()
        || 'Looking for models loaded by LM Studio, Ollama, and other apps.';
      mode = 'loading';
    } else if (gpuRescanPending) {
      title = 'Scanning Loaded Models on GPU…';
      detail = formatStatusTraceDetail()
        || 'Scanning the GPU for DFlash and external app models. Cards will appear here when ready.';
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
    lastStatusTrace = Array.isArray(data?.status_trace) ? data.status_trace : lastStatusTrace;
    lastStatusBuildMs = Number(data?.status_build_ms || 0);
    return true;
  }

  let suppressExternalEmptyDebounce = false;

  function mergeExternalGpuLoads(rows) {
    const next = Array.isArray(rows) ? rows : [];
    if (next.length) {
      externalGpuLoads = next;
      externalMissingPolls = 0;
      return;
    }
    if (suppressExternalEmptyDebounce || !externalGpuLoads.length) {
      externalGpuLoads = [];
      externalMissingPolls = 0;
      return;
    }
    const hadLoading = externalGpuLoads.some((row) => row?.card_state === 'loading');
    const gracePolls = enginesNeedFastRefresh() ? 12 : (hadLoading ? 6 : 8);
    externalMissingPolls += 1;
    if (externalMissingPolls > gracePolls) {
      externalGpuLoads = [];
      externalMissingPolls = 0;
    }
  }

  let externalFetchPromise = null;
  let externalPollEarliestMs = 0;

  async function refreshExternalGpuLoads(shouldRender = true, { force = false, fresh = false } = {}) {
    const now = Date.now();
    if (!force && now < externalPollEarliestMs) return externalFetchPromise;
    if (externalFetchPromise && !force) return externalFetchPromise;
    if (externalFetchPromise && force) {
      try { await externalFetchPromise; } catch { /* start a new scan */ }
    }
    externalPollEarliestMs = now + (enginesNeedFastRefresh() ? 1500 : 4000);
    const showScanNotice = !externalInitialFetchDone;
    if (showScanNotice) {
      externalFetchPending = true;
      updateEnginePageNotice();
    }
    const useFresh = Boolean(fresh);
    externalFetchPromise = (async () => {
      try {
        const data = await api(`/api/servers?include_external=1${useFresh ? '&fresh=1' : ''}`);
        lastStatusTrace = Array.isArray(data?.status_trace) ? data.status_trace : lastStatusTrace;
        lastStatusBuildMs = Number(data?.status_build_ms || 0);
        externalScanError = String(data?.external_scan_error || '').trim();
        mergeExternalGpuLoads(data.external_gpu_loads);
        if (shouldRender) renderCards();
        return data;
      } catch {
        /* keep previous external cards */
        return null;
      } finally {
        externalInitialFetchDone = true;
        if (showScanNotice) {
          externalFetchPending = false;
          updateEnginePageNotice();
        }
        externalFetchPromise = null;
      }
    })();
    return externalFetchPromise;
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

  function modelHasReasoning(model) {
    if (model?.reasoning === true) return true;
    const caps = Array.isArray(model?.capabilities) ? model.capabilities : [];
    return caps.includes('reasoning');
  }

  function syncInspectorReasoningVisibility(model) {
    const group = document.getElementById('inspectorReasoningGroup');
    if (!group) return;
    const reasoning = !!model && modelHasReasoning(model);
    group.classList.toggle('hidden', !reasoning);
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
    const selectedEntry = selectedLoadedEntry();
    const selectedCardModel = selectedEntry?.server?.id === serverId
      ? modelFromLoadedEntry(selectedEntry.server, selectedEntry.row)
      : null;
    const model = selectedCardModel || (selected?.server_id === serverId ? selected : null);
    if (!model) {
      toast('The loaded model could not be identified', false);
      return;
    }
    const button = document.getElementById('inspectorReloadBtn');
    if (button) button.disabled = true;
    try {
      await flushInspectorSave();
      // Router-level flags (context, GPU layers, flash attention, reasoning
      // effort) only take effect when the engine process restarts. Stop the
      // router, then the load below boots it fresh with the saved settings.
      await stopServer(serverId);
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
  function normalizeTargetPath(value) {
    return String(value || '').replace(/\\/g, '/').trim().toLowerCase();
  }

  function entryTargetPath(server, row) {
    const stackPath = Array.isArray(row?.stack_details)
      ? row.stack_details.find((part) => String(part?.role || '').toLowerCase() === 'target')?.path
      : '';
    const candidates = [
      stackPath,
      row?.path,
      row?.model_path,
      server?.target_path,
      server?.model_catalog?.target_path,
    ];
    for (const value of candidates) {
      const norm = normalizeTargetPath(value);
      if (norm && norm.endsWith('.gguf')) return norm;
    }
    return normalizeTargetPath(candidates.find(Boolean) || '');
  }

  function modelTargetPath(model) {
    return normalizeTargetPath(model?.path || model?.model_path || '');
  }

  function loadedEntryRank(entry) {
    const { server, row } = entry || {};
    let rank = 0;
    if (row?.dflash_stack) rank += 30;
    if (String(server?.profile || '').toLowerCase().includes('dflash')) rank += 20;
    if (String(server?.id || '').toLowerCase().includes('dflash')) rank += 10;
    const stats = row?.inference_stats || server?.inference_stats || {};
    if (Array.isArray(stats.recent_completions) && stats.recent_completions.length) rank += 5;
    if (server?.status === 'loaded') rank += 2;
    return rank;
  }

  function dedupeLoadedEntriesByTarget(entries) {
    const externals = [];
    const byKey = new Map();
    for (const entry of entries) {
      if (isExternalEntry(entry)) {
        externals.push(entry);
        continue;
      }
      const target = entryTargetPath(entry.server, entry.row);
      const key = target && target.endsWith('.gguf') ? `target:${target}` : `server:${entry.server?.id || ''}`;
      const prev = byKey.get(key);
      if (!prev || loadedEntryRank(entry) > loadedEntryRank(prev)) {
        byKey.set(key, entry);
      }
    }
    return [...byKey.values(), ...externals];
  }

  function serversSharingTarget(targetPath, excludeServerId = '') {
    const target = normalizeTargetPath(targetPath);
    if (!target) return [];
    return (allServers || servers)
      .filter((server) => server?.id && server.id !== excludeServerId && server.status === 'loaded')
      .filter((server) => {
        const cards = loadedRowsForServer(server);
        if (cards.some((row) => entryTargetPath(server, row) === target)) return true;
        if (normalizeTargetPath(server.target_path) === target) return true;
        return normalizeTargetPath(server.model_catalog?.target_path) === target;
      })
      .map((server) => server.id);
  }

  function findLiveServerForModel(model) {
    if (!model) return null;
    const modelPath = modelTargetPath(model);
    for (const server of allServers || servers) {
      if (modelAlreadyLiveOnServer(model, server)) return server;
      if (!modelPath || server.status !== 'loaded') continue;
      const cards = Array.isArray(server.visible_cards) ? server.visible_cards : [];
      if (cards.some((card) => entryTargetPath(server, card) === modelPath)) return server;
      if (normalizeTargetPath(server.target_path) === modelPath) return server;
      if (normalizeTargetPath(server.model_catalog?.target_path) === modelPath) return server;
    }
    return null;
  }

  function catalogLoadModelId(model) {
    const raw = String(model?.model_id || model?.id || '').trim();
    if (raw.toLowerCase().startsWith('library-file:')) {
      const stripped = raw.slice('library-file:'.length).trim();
      if (stripped) return stripped;
    }
    if (raw && !raw.includes(':')) return raw;
    const file = String(model?.filename || model?.path || '').replace(/\\/g, '/').split('/').pop() || '';
    return file.replace(/\.gguf$/i, '');
  }

  function shouldSendModelPath(model, serverId) {
    if (!model?.path) return false;
    if (model.plain_gguf && !model.library_file) return true;
    const profile = catalogModelForServer(serverId);
    const server = (allServers || servers).find((row) => row.id === serverId);
    const configuredPath = profile?.path
      || server?.target_path
      || server?.model_catalog?.target_path
      || '';
    if (!configuredPath) return true;
    if (normalizeModelPath(configuredPath) !== normalizeModelPath(model.path)) return true;
    return !(model.server_id && model.server_id === serverId);
  }

  function modelAlreadyLiveOnServer(model, server) {
    if (!model || !server) return false;
    const modelPath = modelTargetPath(model);
    if (modelPath && server.status === 'loaded') {
      const cards = Array.isArray(server.visible_cards) ? server.visible_cards : [];
      if (cards.some((card) => entryTargetPath(server, card) === modelPath)) return true;
      if (normalizeTargetPath(server.target_path) === modelPath) return true;
      if (normalizeTargetPath(server.model_catalog?.target_path) === modelPath) return true;
    }
    const loaded = [
      ...(Array.isArray(server.loaded_models) ? server.loaded_models : []),
      server.active_model_id,
    ].map((value) => String(value || '').replace(/\\/g, '/').trim().toLowerCase().replace(/^library-file:/, ''))
      .filter(Boolean);
    if (!loaded.length && server.status !== 'loaded') return false;
    const tokens = [catalogLoadModelId(model), model.id, model.model_id, model.filename, model.path]
      .map((value) => String(value || '').replace(/\\/g, '/').trim().toLowerCase().replace(/^library-file:/, ''))
      .filter(Boolean);
    const all = new Set(tokens.flatMap((token) => [token, token.split('/').pop()].filter(Boolean)));
    return loaded.some((row) => all.has(row) || all.has(row.split('/').pop()));
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
    renderInspectorSelectionState(true);
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
    const url = gatewayUrl || server.reachable_url || '';
    const path = row.path || '';
    const identifier = row?.label || server?.label || row?.filename || server?.model_id || row?.model_id || row?.model_name || row?.id || '';
    const isEmbedding = server.engine_mode === 'embedding'
      || server.model_kind === 'embedding'
      || row.model_kind === 'embedding'
      || EMBEDDING_PROFILES.has(server.profile);

    menu.innerHTML = `
      <button type="button" data-cmd="details">Show details</button>
      <button type="button" data-cmd="runtime">Show runtime settings</button>
      <button type="button" data-cmd="copy-url"${url ? '' : ' disabled'}>Copy API URL</button>
      <button type="button" data-cmd="copy-path"${path ? '' : ' disabled'}>Copy model path</button>
      <button type="button" data-cmd="copy-identifier"${identifier ? '' : ' disabled'} title="Copy the model identifier">Copy identifier</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <hr>
      <button type="button" data-cmd="goto-library" title="Open the same model in the Model library to load it, set it up or delete it">Go to model in Model library</button>
      <hr>
      <button type="button" data-cmd="unload"${ready && row.ejectable ? '' : ' disabled'} title="${isEmbedding ? 'Stop embedding engine and unload its model' : 'Unload model'}">Unload</button>
      <button type="button" data-cmd="cancel"${loading && row.ejectable ? '' : ' disabled'}>Cancel load</button>`;

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
      const url = gatewayUrl || server.reachable_url;
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
    if (cmd === 'copy-identifier') {
      const identifier = row?.label || server?.label || row?.filename || server?.model_id || row?.model_id || row?.model_name || row?.id || '';
      if (!identifier) return;
      await navigator.clipboard.writeText(identifier.replace(/^(stack-capable|library-file|ollama):/i, ''));
      toast('Model name copied');
      return;
    }
    if (cmd === 'goto-library') {
      if (window.DFlashShell?.setView) window.DFlashShell.setView('models');
      const found = await window.DFlashModelsLive?.revealModelFromEngineCard?.({
        path: row?.model_path || row?.path || server?.model_path || '',
        serverId: server?.id || row?.server_id || '',
        modelId: server?.model_id || row?.model_id || row?.model_name || row?.id || '',
        label: server?.label || row?.label || '',
      });
      if (!found) toast('This model is not in the Model library', false);
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

  function dflashLoadedCount() {
    return servers.filter((s) => s.status === 'loaded').length;
  }

  function loadedServerCount() {
    return dflashLoadedCount() + externalGpuLoads.length;
  }

  function fallbackLoadedRow(server, modelId) {
    const token = String(modelId || '').trim();
    const normalized = token.replace(/\\/g, '/');
    const basename = normalized.split('/').pop() || token;
    const warming = serverIsWarming(server);
    const progress = normalizeLoadProgress(server?.load_progress);
    return {
      role: 'loaded-model',
      id: token,
      model_id: token,
      label: basename || server?.label || 'Loaded model',
      title: basename || token.replace(/-/g, ' ') || server?.label || 'Loaded model',
      subtitle: token ? `API: ${token}` : '',
      path: /\.gguf$/i.test(basename) || normalized.includes('/') ? token : '',
      card_state: warming ? 'loading' : 'ready',
      progress: progress.pct ?? server?.load_progress ?? null,
      progress_detail: progress.detail || '',
      warming: Boolean(server?.warming || server?.runtime_id === 'freetoken'),
      ejectable: true,
      plain_llm: true,
      is_adhoc: true,
      inference_stats: server?.inference_stats || {},
    };
  }

  function bootingRowForServer(server) {
    if (!serverIsWarming(server)) return null;
    const modelToken = String(server.model_id || server.active_model_id || '').trim();
    const modelPath = String(server.model_path || '').trim();
    const normalized = modelPath.replace(/\\/g, '/');
    const basename = normalized.split('/').pop() || modelToken.replace(/-/g, ' ');
    const progress = normalizeLoadProgress(server.load_progress);
    return {
      role: 'loaded-model',
      id: modelToken || modelPath || server.id,
      model_id: modelToken,
      label: basename || server.label || 'Model',
      title: basename || server.label || 'Model',
      subtitle: modelPath || modelToken,
      path: modelPath,
      card_state: 'loading',
      progress: progress.pct ?? server.load_progress ?? null,
      progress_detail: progress.detail || '',
      warming: Boolean(server.warming || server.runtime_id === 'freetoken'),
      ejectable: true,
      plain_llm: true,
      is_adhoc: true,
      inference_stats: server?.inference_stats || {},
    };
  }

  function loadedRowsForServer(server) {
    const cards = Array.isArray(server?.visible_cards) ? server.visible_cards.slice() : [];
    const loaded = Array.isArray(server?.loaded_models) ? server.loaded_models : [];
    const serverProgress = normalizeLoadProgress(server?.load_progress);
    const enrichLoadingRow = (row) => {
      if (row?.card_state !== 'loading') return row;
      const rowProgress = normalizeLoadProgress(row?.progress ?? row?.load_progress);
      const pct = rowProgress.pct ?? serverProgress.pct ?? null;
      return {
        ...row,
        progress: pct ?? row?.progress ?? null,
        progress_detail: rowProgress.detail || serverProgress.detail || row?.progress_detail || '',
        warming: Boolean(row?.warming || server?.warming || server?.runtime_id === 'freetoken'),
      };
    };
    if (!loaded.length) {
      if (cards.some((row) => row?.card_state === 'loading')) {
        return cards.map(enrichLoadingRow);
      }
      const bootingRow = bootingRowForServer(server);
      return bootingRow ? [bootingRow] : cards;
    }
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
    return cards.map(enrichLoadingRow);
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
    return servers.filter((s) => s.status === 'booting' || s.booting || s.warming).length;
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
    return dedupeLoadedEntriesByTarget(result);
  }

  function selectedLoadedEntry(entries = collectLoadedEntries()) {
    if (!selectedLoadedKey) return null;
    return entries.find(({ server, row }) => loadedCardKey(server, row) === selectedLoadedKey) || null;
  }

  function clearLoadedCardSelection() {
    selectedLoadedKey = '';
    localStorage.removeItem('dflashConsole.selectedLoadedKey');
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
    const loadingCount = collectLoadedEntries().filter(({ row }) => row?.card_state === 'loading').length;
    const readyCount = Math.max(0, count - loadingCount);
    if (count === 0) el.textContent = 'No models loaded on GPU';
    else if (loadingCount > 0 && readyCount > 0) {
      el.textContent = `${readyCount} loaded · ${loadingCount} loading on GPU`;
    } else if (loadingCount > 0 && loadingCount === count) {
      el.textContent = loadingCount === 1 ? '1 model loading on GPU' : `${loadingCount} models loading on GPU`;
    } else if (count === 1) el.textContent = '1 model loaded on GPU';
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

  let engineCardsManualRefreshInFlight = false;

  async function manualRefreshEngineCards() {
    if (engineCardsManualRefreshInFlight) return;
    const btn = document.getElementById('engineCardsRefreshBtn');
    const meta = document.getElementById('engineCardsRefreshMeta');
    engineCardsManualRefreshInFlight = true;
    gpuRescanPending = true;
    updateEnginePageNotice();
    renderCards();
    btn?.classList.add('is-spinning');
    btn?.setAttribute('disabled', 'true');
    if (meta) meta.textContent = '…';
    const started = performance.now();
    try {
      externalPollEarliestMs = 0;
      await refresh(true, { includeExternal: true, fresh: true });
      const ms = Math.round(performance.now() - started);
      if (meta) meta.textContent = `${ms} ms`;
      btn?.setAttribute('title', `Last manual refresh: ${ms} ms`);
    } catch (err) {
      if (meta) meta.textContent = '';
      toast(err?.message || 'Refresh failed', false);
    } finally {
      gpuRescanPending = false;
      engineCardsManualRefreshInFlight = false;
      btn?.classList.remove('is-spinning');
      btn?.removeAttribute('disabled');
      updateEnginePageNotice();
      renderCards();
    }
  }

  function serverStatusLabel(server) {
    if (!server) return 'Stopped';
    if (server.status === 'error') return server.boot_error || 'Error';
    if (serverIsWarming(server)) {
      const progress = normalizeLoadProgress(server.load_progress);
      if (server.warming || server.runtime_id === 'freetoken') {
        return progress.pct != null ? `Warming ${Math.round(progress.pct)}%` : 'Warming…';
      }
      return progress.pct != null ? `Loading ${Math.round(progress.pct)}%` : 'Loading…';
    }
    if (server.status === 'loaded') return 'Loaded';
    if (server.running) return 'Idle';
    return 'Stopped';
  }

  function aggregateStatusLabel() {
    const dflashLoaded = dflashLoadedCount();
    const booting = bootingServerCount();
    const starting = [...serverActions.values()].filter((a) => a === 'starting').length;
    const loading = pendingLoads.size;
    const stopping = [...serverActions.values()].filter((a) => a === 'stopping').length;
    const ejecting = [...serverActions.values()].filter((a) => a === 'ejecting').length;
    const active = activeServer();
    const engineLive = serverIsLive(active);
    if (stopping === 1 && dflashLoaded === 0 && booting === 0) return 'Stopping…';
    if (stopping > 1) return `${stopping} engines stopping`;
    if (ejecting === 1 && dflashLoaded <= 1) return 'Unloading…';
    if (ejecting > 0) return `${ejecting} unloading · ${dflashLoaded} loaded`;
    if (starting === 1 && dflashLoaded === 0 && booting === 0) return 'Starting engine…';
    if (starting > 0) return `${starting} starting · ${dflashLoaded} loaded`;
    if (hasVisibleLoadingCards() || loading > 0 || booting > 0) {
      if (dflashLoaded > 1) return `${dflashLoaded} models loaded`;
      if (dflashLoaded === 1) return '1 model loaded';
      if (engineLive || active?.running || active?.status === 'booting') return 'Running';
    }
    if (loading > 0 && booting > 0 && dflashLoaded > 0) return `${dflashLoaded} loaded · ${Math.max(loading, booting)} loading`;
    if (loading > 1 || booting > 1) return `${Math.max(loading, booting)} models loading`;
    if (loading === 1) {
      const [serverId] = pendingLoads.keys();
      const meta = pendingLoads.get(serverId);
      const server = servers.find((s) => s.id === serverId);
      const progress = normalizeLoadProgress(server?.load_progress);
      if (progress.pct != null) {
        const label = meta?.label || server?.model_id || 'Model';
        return `${label} ${Math.round(progress.pct)}%`;
      }
      return meta?.label ? `${meta.label}` : 'Running';
    }
    if (booting === 1) {
      const warmingServer = servers.find((s) => s.warming || s.booting || s.status === 'booting');
      if (warmingServer) {
        const progress = normalizeLoadProgress(warmingServer.load_progress);
        const label = String(
          warmingServer.model_id
          || warmingServer.active_model_id
          || warmingServer.model_path
          || 'Model',
        ).split(/[\\/]/).pop().replace(/-/g, ' ');
        if (warmingServer.warming || warmingServer.runtime_id === 'freetoken') {
          return progress.pct != null ? `Warming ${label} ${Math.round(progress.pct)}%` : `Warming ${label}`;
        }
        if (progress.pct != null) return `${label} ${Math.round(progress.pct)}%`;
        return label;
      }
      return 'Running';
    }
    if (dflashLoaded >= 1 && engineLive) return 'Running';
    if (dflashLoaded >= 1) return 'Loaded';
    if (engineLive) return 'Running (idle)';
    if (active?.status === 'booting') return 'Loading…';
    return 'Stopped';
  }

  function detailBadge(source, role) {
    if (role === 'draft-dflash') return 'DFlash 1 draft';
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
    const apiShown = gatewayUrl || server.reachable_url;
    if (apiShown) lines.push(`API: ${apiShown}`);
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

  function dflashLogoLabel(label = 'DFlash 1') {
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

  function accelerationBadge(row) {
    if (!row?.acceleration_expected) return '';
    const active = row.acceleration_mode === 'dflash' && row.draft_loaded === true;
    const unknown = row.draft_status === 'unknown' || row.acceleration_mode === 'unknown';
    const needsRepair = row.draft_status === 'repair_required';
    const label = active
      ? 'DFlash active'
      : (unknown ? 'DFlash status unknown' : (needsRepair ? 'Draft required · repair' : 'DFlash stack ready'));
    const title = active
      ? 'A compatible draft model is part of this loaded stack.'
      : (unknown
        ? 'This external llama-server did not expose its draft argument; the live draft state cannot be verified.'
        : (needsRepair
          ? 'This DFlash profile cannot load until a matching draft accelerator is attached.'
          : 'This DFlash profile has a matching draft accelerator ready for its next load.'));
    const tone = active ? 'green' : (needsRepair ? 'orange' : 'yellow');
    return `<span class="lm-tag ${tone}" title="${escapeHtml(title)}">${label}</span>`;
  }

  function cardUsesDflashStack(row) {
    if (row?.external) return false;
    if (row?.dflash_stack === true) return true;
    const details = Array.isArray(row.stack_details) ? row.stack_details : [];
    if (details.some((part) => String(part?.role || '').startsWith('draft'))) return true;
    if (row?.draft_path) return true;
    const hay = `${row?.title || ''} ${row?.id || ''} ${row?.label || ''} ${row?.display_name || ''}`.toLowerCase();
    if (/dflash|dspark/.test(hay) && !isAcceleratorOnlyModel(row)) {
      return true;
    }
    if (row?.is_adhoc || row?.plain_llm || row?.dflash_stack === false) return false;
    return false;
  }

  function cardModelPresentation(row) {
    const details = Array.isArray(row?.stack_details) ? row.stack_details : [];
    const draft = details.find((part) => String(part?.role || '').startsWith('draft'));
    const target = details.find((part) => String(part?.role || '') === 'target');
    return {
      ...row,
      label: row?.title || row?.model_name || row?.label || row?.id || '',
      filename: row?.filename || target?.name || row?.model_name || '',
      path: row?.path || target?.path || '',
      draft_path: row?.draft_path || draft?.path || '',
      draft_filename: row?.draft_filename || draft?.name || '',
      draft_size_gb: row?.draft_size_gb ?? draft?.size_gb,
      dflash_stack: cardUsesDflashStack(row),
    };
  }

  function roleBadge(row) {
    if (row.external) {
      // The external app name is shown as a centered banner at the top of the
      // card (see externalPrompt), not as a tag in the labels row.
      return '';
    }
    const loadedBy = row?.loaded_by || row?.app_label || '';
    const appBadge = loadedBy && !/^dflash\s+console$/i.test(String(loadedBy).trim())
      ? `<span class="lm-tag orange" title="Requested by ${escapeHtml(loadedBy)}">${escapeHtml(loadedBy)}</span>`
      : '';
    const shared = window.DFlashModelCard?.classificationTags?.(cardModelPresentation(row));
    if (shared) return `${shared}${appBadge}`;
    if (row.role === 'draft-dflash') {
      const gen = row?.dflash_generation_label || window.DFlashModelGroups?.acceleratorGenerationLabel?.(row) || 'DFlash 1';
      return `${dflashLogoLabel(`${gen} accelerator`)}${acceleratorBadge(`${gen} draft accelerator; not a target model`)}${appBadge}`;
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
      return `${dflashLogoLabel('DFlash 1 stack')}${appBadge}`;
    }
    if (row.card_state === 'ready' || row.card_state === 'loading' || row.role === 'alias' || kind?.kind === 'llm') {
      return `<span class="lm-tag cyan" title="Standard LLM checkpoint">LLM</span>${appBadge}`;
    }
    if (row.source === 'lmstudio') return `<span class="lm-tag blue">LM Studio</span>${appBadge}`;
    return `<span class="lm-tag blue" title="Managed by DFlash Console">LLM</span>${appBadge}`;
  }

  function inferCardDetail(row) {
    if (row?.external) {
      const raw = String(row.card_detail || row.subtitle || '').trim();
      if (raw) return raw;
      const kind = inferModelKind(row);
      const parts = [];
      if (kind?.kind === 'llm') {
        parts.push('GGUF');
        const name = cardDisplayName(row);
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

  function formatContextTokens(value) {
    const tokens = Number(value);
    if (!Number.isFinite(tokens) || tokens <= 0) return '';
    if (tokens >= 1024) {
      const k = tokens / 1024;
      return Number.isInteger(k) ? `${k}K` : `${k.toFixed(1).replace(/\.0$/, '')}K`;
    }
    return String(Math.round(tokens));
  }

  function cardContextSize(row, server) {
    const stats = row?.inference_stats || server?.inference_stats || {};
    const candidates = [row?.context_size, server?.context_size, stats.context_tokens];
    for (const value of candidates) {
      const tokens = Number(value);
      if (Number.isFinite(tokens) && tokens > 0) return Math.round(tokens);
    }
    return null;
  }

  function cardContextMetric(row, server) {
    const tokens = cardContextSize(row, server);
    if (!tokens) return '';
    const title = `Loaded with ${tokens.toLocaleString()} token context window`;
    return `<span class="lm-model-card-metric lm-model-card-tag-metric lm-ctx-metric" title="${escapeHtml(title)}"><span class="lbl">CTX</span>${escapeHtml(formatContextTokens(tokens))}</span>`;
  }

  function cardTagMetricsHtml(row, server) {
    const ctx = cardContextMetric(row, server);
    const vramPct = cardVramPctMetric(row);
    const disk = formatCardGb(cardSizeGb(row));
    const label = 'Disk';
    const diskSpan = disk
      ? `<span class="lm-model-card-metric lm-model-card-tag-metric"><span class="lbl">${label}</span>${escapeHtml(disk)}</span>`
      : '';
    if (!ctx && !vramPct && !diskSpan) return '';
    return `${ctx}${vramPct}${diskSpan}`;
  }

  function gpuTotalVramGb(gpuIndex) {
    const idx = Number(gpuIndex);
    if (Number.isFinite(idx) && idx >= 0) {
      const gpu = gpus.find((g) => Number(g.index) === idx);
      const total = Number(gpu?.vram_total_gb ?? gpu?.vram_gb);
      if (Number.isFinite(total) && total > 0) return total;
    }
    return totalVramGb;
  }

  function formatVramGb(value) {
    const gb = Number(value);
    if (!Number.isFinite(gb) || gb <= 0) return '';
    if (gb < 10) return `${gb.toFixed(1).replace(/\.0$/, '')} GB`;
    return `${Math.round(gb)} GB`;
  }

  function cardVramPctMetric(row) {
    const isExternal = !!row?.external;
    const used = Number(row?.vram_gb) || (!isExternal ? cardSizeGb(row) : 0);
    if (!used || used <= 0) return '';
    const gpuTotal = gpuTotalVramGb(row?.gpu_index);
    if (!gpuTotal) return '';
    const pct = (used / gpuTotal) * 100;
    const usedLabel = formatVramGb(used);
    const title = `Uses ${usedLabel} on this GPU (~${pct.toFixed(1)}% of ${gpuTotal} GB)`;
    const body = isExternal ? usedLabel : `${Math.round(pct)}%`;
    return `<span class="lm-model-card-metric lm-model-card-tag-metric lm-vram-pct" title="${escapeHtml(title)}"><span class="lbl">VRAM</span>${escapeHtml(body)}</span>`;
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
    if (entry.tokens_per_second != null) parts.push(`DECODE ${entry.tokens_per_second} t/s`);
    return parts.join(' · ');
  }

  function recentCompletionsTitle(history) {
    const rows = Array.isArray(history) ? history.slice(0, 3) : [];
    if (!rows.length) return 'Last completion';
    return `Recent completions\n${rows.map((entry, index) => `${index + 1}. ${tokenSummary(entry)}`).join('\n')}`;
  }

  const lastTokenMetrics = new Map();

  function tokenMetricSnapshot(entry) {
    if (!entry) return null;
    const snapshot = {};
    for (const key of ['prompt_tokens', 'generation_tokens', 'tokens_per_second']) {
      if (entry[key] != null) snapshot[key] = entry[key];
    }
    return Object.keys(snapshot).length ? snapshot : null;
  }

  function mergedTokenMetric(...entries) {
    const merged = {};
    for (const entry of entries) {
      const snapshot = tokenMetricSnapshot(entry);
      if (!snapshot) continue;
      for (const key of ['prompt_tokens', 'generation_tokens', 'tokens_per_second']) {
        if (merged[key] == null && snapshot[key] != null) merged[key] = snapshot[key];
      }
    }
    return Object.keys(merged).length ? merged : null;
  }

  function cardTokenMetricGroup(slot, { live = false, recent = [] } = {}) {
    if (live) {
      const outTok = Number(slot.generating_tokens ?? 0) || 0;
      const inPrefill = !!slot.generating && outTok <= 0;
      const promptTok = slot.prompt_tokens;
      const prefillTok = slot.prefill_tokens;
      let inputTok = prefillTok ?? promptTok ?? 0;
      if (inPrefill && promptTok != null && prefillTok != null && Number(prefillTok) < Number(promptTok)) {
        inputTok = `${prefillTok}/${promptTok}`;
      }
      const speed = inPrefill
        ? slot.prefill_tokens_per_second
        : slot.generating_tokens_per_second;
      const speedText = (speed == null || !Number.isFinite(Number(speed)))
        ? '—'
        : `${speed} t/s`;
      const speedLabel = inPrefill ? 'PREFILL' : 'DECODE';
      const title = inPrefill ? 'Reading the prompt into context' : 'Live generation';
      return `
        <span class="lm-model-card-token-metric is-live lm-model-card-token-generating" title="${escapeHtml(title)}">
          <span class="lbl">IN</span>${escapeHtml(String(inputTok))}
          <span class="lm-model-card-token-separator">·</span>
          <span class="lbl">OUT</span>${escapeHtml(String(outTok))}
          <span class="lm-model-card-token-separator">·</span>
          <span class="lbl">${speedLabel}</span>${escapeHtml(speedText)}
        </span>`;
    }
    const parts = [];
    if (slot.prompt_tokens != null) parts.push(`IN ${slot.prompt_tokens}`);
    if (slot.generation_tokens != null) parts.push(`OUT ${slot.generation_tokens}`);
    if (slot.tokens_per_second != null) parts.push(`DECODE ${slot.tokens_per_second} t/s`);
    const text = parts.join(' · ');
    if (!text) return '';
    return `<span class="lm-model-card-token-metric lm-model-card-token-last" title="${escapeHtml(recentCompletionsTitle(recent))}"><span class="lbl">LAST</span>${escapeHtml(text)}</span>`;
  }

  function inferenceIsGenerating(stats) {
    return !!stats?.generating
      || (Array.isArray(stats?.slots) && stats.slots.some((slot) => slot?.generating));
  }

  function cardIsGenerating(row, server) {
    const stats = row?.inference_stats || server?.inference_stats || {};
    return inferenceIsGenerating(stats);
  }

  function cardTokenMetricsRow({ server, row }) {
    const isExternal = !!(row?.external || server?.external);
    const stats = row?.inference_stats || server?.inference_stats || {};
    if (
      !isExternal
      && row?.card_state !== 'ready'
      && server?.status !== 'loaded'
      && !inferenceIsGenerating(stats)
    ) return '';
    const slots = slotInferenceStats(stats);
    if (!slots.length && isExternal) return '';
    const visibleSlots = slots.length
      ? slots
      : [{ slot_id: 0, ...stats, generating: !!stats.generating }];
    const primarySlot = visibleSlots.find((slot) => slot?.generating) || visibleSlots[0];
    const metricKey = loadedCardKey(server, row);
    const recent = Array.isArray(stats.recent_completions) ? stats.recent_completions : [];

    const groups = [];
    const metrics = [];
    if (primarySlot.generating) {
      metrics.push(cardTokenMetricGroup(primarySlot, { live: true }));
      const outTok = Number(primarySlot.generating_tokens ?? 0) || 0;
      if (outTok > 0 && metricKey) {
        // Keep the newest real count available for the brief transition where
        // the slot becomes idle before the completion summary is published.
        lastTokenMetrics.set(metricKey, {
          prompt_tokens: primarySlot.prompt_tokens,
          generation_tokens: outTok,
          tokens_per_second: primarySlot.generating_tokens_per_second,
        });
      }
      const liveSpeed = primarySlot.generating_tokens_per_second ?? primarySlot.prefill_tokens_per_second;
      const liveActive = outTok > 0 || (liveSpeed != null && Number.isFinite(Number(liveSpeed)));
      const last = recent[0] || {};
      const hasLast = last.prompt_tokens != null || last.generation_tokens != null;
      if (!liveActive && hasLast) {
        metrics.push(cardTokenMetricGroup(last, {
          live: false,
          recent,
        }));
      }
    }

    if (!primarySlot.generating) {
      // Keep the row mounted between polls and retain the last completed
      // request if llama-server briefly reports an empty idle slot.
      const last = mergedTokenMetric(
        primarySlot,
        lastTokenMetrics.get(metricKey),
        recent[0],
        { prompt_tokens: 0, generation_tokens: 0 },
      );
      if (last && metricKey) lastTokenMetrics.set(metricKey, last);
      metrics.push(cardTokenMetricGroup(last, {
        live: false,
        recent,
      }));
    }
    if (metrics.length) {
      groups.push(`<span class="lm-model-card-slot-metric-group">${metrics.join('')}</span>`);
    }

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
    const sharedDetails = window.DFlashModelCard?.detailsHtml?.(cardModelPresentation(row), {
      includeTarget: true,
      includeAccelerator: true,
    }) || '';
    const isExternal = !!(row?.external || server?.external);
    const kindBadge = modelKindBadge(row);
    return `
      <div class="lm-model-card-center${hasTokenRow ? ' has-token-row' : ''}">
        <div class="lm-model-card-center-row lm-model-card-title-row">
          <span class="lm-model-card-identity">
            <span class="lm-model-card-name-line">
              <span class="lm-model-path">${escapeHtml(cardDisplayName(row, server))}</span>
              ${isExternal ? kindBadge : ''}
            </span>
          </span>
          <span class="lm-model-card-labels">
            ${installedBadge}
            ${statusBadge}
            ${isExternal ? '' : kindBadge}
            ${accelerationBadge(row)}
            ${roleBadge(row)}
            ${cardDetailHtml(row)}
          </span>
        </div>
        ${sharedDetails}
        ${tokenRow}
      </div>`;
  }

  function emptyMessage(server) {
    const action = getServerAction(server?.id);
    if (action === 'stopping') return 'Stopping server…';
    if (action === 'ejecting') return 'Unloading model…';
    if (action === 'starting') return 'Starting engine…';
    if (action === 'loading' || serverIsWarming(server)) {
      const progress = normalizeLoadProgress(server?.load_progress);
      if (server?.warming || server?.runtime_id === 'freetoken') {
        return progress.pct != null
          ? `Warming expert banks… ${Math.round(progress.pct)}%`
          : (progress.detail || 'Warming expert banks in WSL…');
      }
      return 'Loading model…';
    }
    if (server?.acceleration_expected && server?.draft_status === 'repair_required') {
      return 'Draft required. Click Load to choose or download the matching accelerator.';
    }
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
    if (selectedLoadedKey && !selectedLoadedEntry(allEntries)) {
      clearLoadedCardSelection();
    }
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

    // DFlash models always first (loading ones at the very top), then external
    // GPU models below, so Console models stay at the top of the list.
    const orderedEntries = entries.slice().sort((a, b) => {
      const aExt = isExternalEntry(a) ? 1 : 0;
      const bExt = isExternalEntry(b) ? 1 : 0;
      if (aExt !== bExt) return aExt - bExt;
      const aLoad = a.row?.card_state === 'loading' ? 0 : 1;
      const bLoad = b.row?.card_state === 'loading' ? 0 : 1;
      if (aLoad !== bLoad) return aLoad - bLoad;
      return 0;
    });

    wrap.innerHTML = orderedEntries.map(({ server, row }, index) => {
      const isExternal = isExternalEntry({ server, row });
      const prevEntry = orderedEntries[index - 1];
      const prevIsExternal = prevEntry ? isExternalEntry(prevEntry) : null;
      // Horizontal separator between the DFlash models and the external group.
      const groupSep = prevIsExternal === false && isExternal
        ? '<div class="lm-model-card-group-sep" aria-hidden="true"></div>'
        : '';
      const ready = row.card_state === 'ready';
      const loading = row.card_state === 'loading';
      const actionKey = loadedCardKey(server, row);
      const ejecting = getServerAction(actionKey) === 'ejecting';
      const warming = Boolean(row.warming || server.warming || (server.runtime_id === 'freetoken' && loading));
      const rawProgress = row.progress ?? (loading ? server.load_progress : null);
      const progress = loadProgressDisplay(loading, rawProgress, { warming });
      const progressPct = progress.pct;
      const progressKnown = loading && progress.known;
      const loadLabel = warming ? 'Warming' : 'Loading';
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
      const isGenerating = cardIsGenerating(row, server);
      const cardClass = `lm-model-card lm-model-card-compact ${ejecting ? 'ejecting' : ready ? 'ready' : 'loading'}${isGenerating ? ' generating' : ''}${isSelected ? ' selected' : ''}${row.external ? ' external-gpu' : ' dflash-model'}`;
      const cardStyle = loading && progressKnown && progressPct != null
        ? ` style="--card-progress:${progressPct}%"`
        : '';
      const installedBadge = row.external ? '' : '<span class="lm-badge installed">Installed</span>';
      const loadChrome = loading && !isGenerating
        ? `<div class="lm-model-card-load-shell${progressKnown ? '' : ' is-indeterminate'}"${cardStyle} role="status" aria-live="polite">
            <div class="lm-model-card-load-head">
              <span class="lm-model-card-load-label">${loadLabel}<span class="lm-loading-dots"><span>.</span><span>.</span><span>.</span></span></span>
              <span class="lm-model-card-load-percent">${progressKnown && progressPct != null ? `${Math.round(progressPct)}%` : 'Preparing…'}${progress.etaLabel ? ` · ${escapeHtml(progress.etaLabel)}` : ''}</span>
            </div>
            <div class="lm-model-card-load-detail">${escapeHtml(progress.detail || (warming ? 'Building expert banks…' : 'Reading model weights…'))}</div>
            <div class="lm-model-card-load-track" role="progressbar" aria-label="${escapeHtml(`${loadLabel} ${cardDisplayName(row, server)}`)}"${progressKnown && progressPct != null ? ` aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(progressPct)}"` : ' aria-valuemin="0" aria-valuemax="100"'}><div class="lm-model-card-load-fill"></div></div>
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
          : `<span class="lm-badge loading">${progress.label || 'Loading'}</span>`;
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
      // External GPU models are loaded from outside the Console. Always label
      // them clearly as OUTSIDE DFlash (even API-only entries without a local
      // file path, e.g. LM Studio models) so it is obvious which models are
      // inside vs outside the Console. The Copy-to-Console action only applies
      // when there is an actual file to copy.
      const externalPath = row?.model_path || row?.path || '';
      // Importing to the Console works for GGUF model files AND faster-whisper
      // model directories (STT). The import endpoint validates either; only
      // skip obvious file paths that are neither.
      const importablePath = externalPath && (/^\.gguf$/i.test(externalPath) || isImportableSttDir(externalPath)) ? externalPath : '';
      // When the same weights are already in the Console library, show a hint
      // instead of offering Import again.
      const inConsoleLibrary = !!importablePath && window.DFlashModelsLive?.isModelAlreadyImported?.(importablePath) === true;
      const externalPrompt = row.external
        ? `<div class="lm-model-card-external-prompt">
            <span class="lm-external-origin">External</span>
            <span class="lm-external-app-name" title="Loaded outside DFlash Console by ${escapeHtml(cardAppLabel(row))}">${escapeHtml(cardAppLabel(row))}</span>
            <span class="lm-external-prompt-actions">
              ${inConsoleLibrary
                ? `<span class="lm-tag teal" title="Same model is registered in DFlash Console. Load it from Models or the engine dropdown above instead of ${escapeHtml(cardAppLabel(row))}.">In Console library</span>`
                : importablePath
                  ? `<button type="button" class="lm-btn ghost tiny" data-action="copy-to-console" data-path="${escapeHtml(importablePath)}" title="Import this model into the DFlash Console library to manage, load and run it here">Import model to Flash Console</button>`
                  : ''}
            </span>
          </div>`
        : '';

      return `
        ${groupSep}
        <article class="${cardClass}" data-server-id="${escapeHtml(server.id)}" data-role="${escapeHtml(row.role || 'external-gpu')}"${row.external ? ` data-external-pid="${row.pid}"` : ''} role="button" tabindex="0" title="${escapeHtml(hoverTitle)}"${isGenerating ? ' aria-label="Model generating"' : ''}${ejecting ? ' aria-busy="true"' : ''}${cardStyle}>
          ${loadChrome}
          ${ejectChrome}
          ${externalPrompt}
          <div class="lm-model-card-top">
            ${centerBlock}
            <span class="lm-model-card-tags">${missing}</span>
            <div class="lm-model-stats">
              ${cardTagMetricsHtml(row, server)}
              ${action}
            </div>
          </div>
        </article>`;
    }).join('');

    wrap.querySelectorAll('[data-action="copy-to-console"]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const path = btn.getAttribute('data-path');
        if (!path) return;
        // Pass the external card's unload info so the wizard unloads the model
        // from LM Studio before importing (frees the file, avoids stale cards).
        const card = btn.closest('.lm-model-card');
        const pid = card ? Number(card.getAttribute('data-external-pid') || 0) : 0;
        const row = pid ? externalGpuLoads.find((entry) => Number(entry.pid) === pid) : null;
        const result = await window.DFlashModelsLive?.importModelWithWizard?.({
          path,
          name: String(path).split(/[\\/]/).pop() || '',
          unload: row
            ? { pid, api_url: row.api_url || '', model_id: row.model_id || '' }
            : null,
        });
        if (result && !result.canceled) {
          await refresh(true, { fresh: true });
        }
      });
    });

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
    if (window.DFlashModelGroups?.defaultOptionLabel) {
      return window.DFlashModelGroups.defaultOptionLabel(model);
    }
    const parts = [model.label || model.filename || model.id || 'Model'];
    if (model.quant && model.quant !== '—') parts.push(model.quant);
    if (model.size_gb != null) parts.push(`${model.size_gb} GB`);
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
      loadBtn.disabled = !canLoadModel(model) || checking || (plan?.level === 'block');
    }
    if (!model || !key) return;
    if (plan?.level === 'already_loaded') {
      notice.textContent = plan.message || 'This model is already loaded on this engine.';
      notice.classList.remove('hidden', 'is-block', 'is-checking');
      return;
    }
    if (checking || !plan) {
      notice.textContent = 'Checking whether this model fits the selected GPU…';
      notice.classList.remove('hidden');
      notice.classList.add('is-checking');
      return;
    }
    // Only surface the VRAM warning box when the model cannot be loaded
    // (block).  Fits (ok) and tight (warn) proceed without the box.
    if (plan.level !== 'block') return;
    notice.textContent = plan.message || 'GPU memory may be insufficient for this model.';
    notice.classList.remove('hidden');
    notice.classList.add('is-block');
  }

  async function fetchLoadPlan(model, serverId) {
    const sid = serverId || model?.server_id || activeServer()?.id || '';
    if (!sid || !model) return null;
    const params = new URLSearchParams();
    if (model.path || model.model_path) params.set('model_path', model.path || model.model_path);
    if (model.id) params.set('model_id', model.id);
    try {
      return await api(
        `/api/servers/${encodeURIComponent(sid)}/load-plan?${params.toString()}`,
        { timeoutMs: 30000 },
      );
    } catch (err) {
      const detail = dflashRepairDetail(err);
      if (detail?.repair) {
        return { level: 'repair_required', ...detail };
      }
      return {
        level: 'warn',
        message: 'GPU fit could not be checked. Loading may fail if the model exceeds available VRAM.',
      };
    }
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
    try {
      const result = await fetchLoadPlan(model, serverId);
      if (currentLoadPlanKey !== key) return;
      currentLoadPlan = result;
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
    // Ollama models run through the Ollama API — the Console engine cannot
    // load them, so keep them out of the engine model picker.
    const loadEngine = currentLoadEngine();
    const hfFilter = window.DFlashModelsLive?.isHfEngineModel;
    const loadableModels = catalogModels.filter((model) => (
      window.DFlashModelGroups?.isPickerVisibleModel?.(model, catalogModels)
      ?? !isAcceleratorOnlyModel(model)
    ));
    const engineModels = window.DFlashModelGroups?.modelsForLoadPicker?.(
      loadableModels,
      loadEngine,
      hfFilter,
    ) || loadableModels;
    const visibleModels = source
      ? engineModels.filter((m) => String(window.DFlashModelGroups?.sourceIdFor?.(m) || '').trim().toLowerCase() === sourceKey)
      : engineModels;
    if (sourcePick && window.DFlashModelGroups?.sourceOptions && !modelPickerRefreshPaused()) {
      const loadableCatalogModels = window.DFlashModelGroups?.modelsForLoadPicker?.(
        catalogModels.filter((model) => (
          window.DFlashModelGroups?.isPickerVisibleModel?.(model, catalogModels)
          ?? !isAcceleratorOnlyModel(model)
        )),
        loadEngine,
        hfFilter,
      ) || catalogModels;
      sourcePick.innerHTML = ['<option value="">All sources</option>',
        ...window.DFlashModelGroups.sourceOptions(loadableCatalogModels).map(([id, label]) =>
          `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`)].join('');
      sourcePick.value = source;
      sourcePick.disabled = false;
      sourcePick.classList.remove('is-loading');
    }
    const placeholder = ENGINE_MODEL_PLACEHOLDER;
    if (!modelPickerRefreshPaused()) {
      if (window.DFlashModelGroups?.renderGroupedSelectOptions) {
        pick.innerHTML = window.DFlashModelGroups.renderGroupedSelectOptions(visibleModels, {
          catalogKey: modelCatalogKey,
          optionLabel: modelOptionLabel,
          placeholder,
          selectedKey: selectedModelKey,
          consoleFirst: true,
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
    }
    pick.disabled = false;
    pick.classList.remove('is-loading');
    window.DFlashSelectTheme?.syncSelect?.(pick);

    const selected = catalogModels.find((m) => modelCatalogKey(m) === pick.value);
    if (loadBtn) loadBtn.disabled = !canLoadModel(selected);
    // The VRAM/load-plan warning only appears when the user actually presses
    // Load — never automatically on page load or dropdown selection.
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
    clearLoadedCardSelection();
    selectedModelKey = pick?.value || '';
    if (loadBtn) loadBtn.disabled = !canLoadModel(model);
    if (model?.server_id) {
      activeId = model.server_id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
    if (model) {
      await applyModelSelection(model);
      await window.DFlashModelsLive?.selectModel?.(selectedModelKey, { applyInspector: false });
    } else {
      renderInspectorEmptyState();
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
    const loadEngine = currentLoadEngine();
    if ((loadEngine === 'vllm' || loadEngine === 'transformers' || loadEngine === 'freetoken') && model) {
      if (!window.DFlashModelsLive?.isHfEngineModel?.(model)) {
        toast('This engine needs a Hugging Face model folder, not a GGUF file.', false);
        return;
      }
      if (window.DFlashModelsLive?.loadModel) {
        await window.DFlashModelsLive.loadModel({ ...model, runtime_id: loadEngine });
        return;
      }
    }
    if (!canLoadModel(model)) {
      if (model?.path) toast('Pick an engine profile first (use the toolbar toggle), then Load.', false);
      else toast('This model is browse-only — wire it to an engine profile in Settings.', false);
      return;
    }
    // Only now, on Load press, check GPU fit and surface the VRAM warning.
    await refreshLoadPlan(model).catch(() => {});
    if (currentLoadPlan?.level === 'repair_required') {
      await openDflashRepair(
        { apiDetail: currentLoadPlan },
        model,
        model.server_id || activeServer()?.id || '',
      );
      return;
    }
    if (currentLoadPlan?.level === 'block') {
      renderLoadPlanNotice(model); // shows the warning box; the model is not loaded
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

    const label = aggregateStatusLabel();

    if (statusText) {
      statusText.textContent = label;
      const anyActive = dflashLoadedCount() > 0
        || bootingServerCount() > 0
        || serverIsLive(server);
      statusText.className = anyActive ? 'lm-status-running' : 'lm-status-stopped';
    }
    if (toggle) setRunningToggle(serverIsLive(server) && getServerAction(server.id) !== 'stopping');
    if (urlEl) {
      urlEl.textContent = gatewayUrl || server.reachable_url || '—';
      if (!gatewayUrl) void loadGatewayUrl({ rerender: true });
    }
    const picked = selectedCatalogModel();
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
      context_max: parseInt(document.getElementById('inspectorContextMax')?.value || '131072', 10),
      load_settings: {
        gpu_layers: parseInt(document.getElementById('inspectorGpuLayers')?.value || '99', 10),
        cpu_threads: parseInt(document.getElementById('inspectorCpuThreads')?.value || '9', 10),
        eval_batch_size: parseInt(document.getElementById('inspectorEvalBatch')?.value || '2048', 10),
        physical_batch_size: parseInt(document.getElementById('inspectorPhysicalBatch')?.value || '512', 10),
        flash_attention: !!document.getElementById('inspectorFlashAttention')?.checked,
        parallel_slots: parseInt(document.getElementById('inspectorParallelSlots')?.value || '4', 10),
      },
      inference_settings: {
        temperature: parseFloat(document.getElementById('inspectorTemperature')?.value || '0.7'),
        top_p: parseFloat(document.getElementById('inspectorTopP')?.value || '0.9'),
        top_k: parseInt(document.getElementById('inspectorTopK')?.value || '40', 10),
        repeat_penalty: parseFloat(document.getElementById('inspectorRepeatPenalty')?.value || '1.1'),
        max_tokens: parseInt(document.getElementById('inspectorMaxTokens')?.value || '4096', 10),
        reasoning_effort: document.getElementById('inspectorReasoningEffort')?.value || 'auto',
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
    const ctxMaxEl = document.getElementById('inspectorContextMax');
    if (ctxMaxEl) ctxMaxEl.value = server.context_max || ctxMax;

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
    const parallelEl = document.getElementById('inspectorParallelSlots');
    if (parallelEl) parallelEl.value = load.parallel_slots ?? 4;

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
    const reasoningEl = document.getElementById('inspectorReasoningEffort');
    if (reasoningEl) reasoningEl.value = ['auto', 'none', 'low', 'medium', 'high', 'max'].includes(infer.reasoning_effort)
      ? infer.reasoning_effort
      : 'auto';

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
        specHint.textContent = 'Fixed by profile: draft-dflash speculative decoding (DFlash 1 or DFlash 2, depending on accelerator).';
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

    const modalityEl = document.getElementById('inspectorInfoModality');
    if (modalityEl) {
      const MODALITY_LABEL = {
        'llm': 'LLM', 'embedding': 'Embedding', 'speech-to-text': 'Speech-to-text',
        'text-to-speech': 'Text-to-speech', 'vision': 'Vision', 'ocr': 'OCR',
      };
      const modality = String(model.modality || model.model_kind || '').toLowerCase();
      modalityEl.textContent = MODALITY_LABEL[modality] || (model.external ? 'External' : 'LLM');
    }

    const deviceRuleEl = document.getElementById('inspectorInfoDeviceRule');
    if (deviceRuleEl) {
      const runtimeId = String(model.runtime_id || '');
      if (runtimeId && runtimeId !== 'llama-server') {
        deviceRuleEl.textContent = `Per-runtime (${runtimeId})`;
        deviceRuleEl.title = 'Non-llama runtime: device comes from its per-runtime device_policy.';
      } else {
        deviceRuleEl.textContent = 'Global (hardware settings)';
        deviceRuleEl.title = 'llama-server stack: device comes from global hardware_settings.gpu_strategy.';
      }
    }

    const visionRow = document.getElementById('inspectorInfoVisionRow');
    const visionEl = document.getElementById('inspectorInfoVision');
    if (visionRow && visionEl) {
      const caps = Array.isArray(model.capabilities) ? model.capabilities : [];
      const mmproj = String(model.mmproj_path || '').trim();
      const visionCapable = caps.includes('vision') || !!mmproj;
      visionRow.classList.toggle('hidden', false);
      if (visionCapable) {
        visionEl.textContent = mmproj ? 'Yes — mmproj wired' : 'Yes (projector required)';
        visionEl.title = mmproj ? mmproj : 'Vision-capable; add a mmproj projector to enable images.';
      } else {
        visionEl.textContent = 'No';
        visionEl.title = 'Model is not vision-capable; image attachments will be hidden in the Playground.';
      }
    }

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
      if (list.includes('reasoning')) tags.push('<span class="lm-tag yellow" title="This model exposes a thinking/reasoning mode">reasoning</span>');
      list.forEach((cap) => {
        if (cap === 'instruct' || cap === 'tools' || cap === 'ar' || cap === 'dflash' || cap === 'reasoning') return;
        tags.push(`<span class="lm-tag blue">${escapeHtml(cap)}</span>`);
      });
      caps.innerHTML = tags.join('') || '—';
    }
    const reasoningInfoEl = document.getElementById('inspectorInfoReasoning');
    if (reasoningInfoEl) {
      reasoningInfoEl.textContent = modelHasReasoning(model) ? 'Yes — thinking supported' : 'No';
      reasoningInfoEl.title = modelHasReasoning(model)
        ? 'This model exposes a thinking/reasoning mode. Set the reasoning effort in the Runtime tab.'
        : 'Plain chat model: the API returns regular completions without reasoning.';
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

  function renderInspectorSelectionState(hasSelection) {
    const empty = document.getElementById('inspectorEmptyState');
    if (empty) empty.classList.toggle('hidden', hasSelection);
    document.querySelectorAll('.lm-inspector-panel').forEach((panel) => {
      panel.classList.toggle('hidden', !hasSelection);
    });
    document.querySelectorAll('.lm-inspector-tab').forEach((tab) => {
      tab.disabled = !hasSelection;
    });
    if (!hasSelection) {
      document.getElementById('inspectorHeadTitle')?.replaceChildren(document.createTextNode('No model selected'));
      document.getElementById('inspectorReloadNotice')?.classList.add('hidden');
      syncInspectorLoadedState(null);
      syncInspectorReasoningVisibility(null);
    }
  }

  function renderInspectorEmptyState() {
    renderInspectorSelectionState(false);
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
    renderInspectorSelectionState(true);
    syncInspectorReasoningVisibility(model);
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

  function readLlamaSettingsFromForm() {
    return {
      load_settings: {
        gpu_layers: parseInt(document.getElementById('llamaSettingsGpuLayers')?.value || '99', 10),
        cpu_threads: parseInt(document.getElementById('llamaSettingsCpuThreads')?.value || '9', 10),
        eval_batch_size: parseInt(document.getElementById('llamaSettingsEvalBatch')?.value || '2048', 10),
        physical_batch_size: parseInt(document.getElementById('llamaSettingsPhysicalBatch')?.value || '512', 10),
        flash_attention: !!document.getElementById('llamaSettingsFlashAttention')?.checked,
        parallel_slots: parseInt(document.getElementById('llamaSettingsParallelSlots')?.value || '4', 10),
      },
      inference_settings: {
        temperature: parseFloat(document.getElementById('llamaSettingsTemperature')?.value || '0.7'),
        top_p: parseFloat(document.getElementById('llamaSettingsTopP')?.value || '0.9'),
        top_k: parseInt(document.getElementById('llamaSettingsTopK')?.value || '40', 10),
        repeat_penalty: parseFloat(document.getElementById('llamaSettingsRepeatPenalty')?.value || '1.1'),
        max_tokens: parseInt(document.getElementById('llamaSettingsMaxTokens')?.value || '4096', 10),
        reasoning_effort: document.getElementById('llamaSettingsReasoningEffort')?.value || 'auto',
      },
    };
  }

  function fillLlamaSettingsForm(server) {
    if (!server) return;
    const pick = document.getElementById('llamaSettingsPick');
    if (pick) {
      pick.innerHTML = allServers.map((s) =>
        `<option value="${escapeHtml(s.id)}"${s.id === (server.id || activeId) ? ' selected' : ''}>${escapeHtml(s.label || s.id)}</option>`,
      ).join('');
    }
    const load = server.load_settings || {};
    const infer = server.inference_settings || {};
    const gpuEl = document.getElementById('llamaSettingsGpuLayers');
    if (gpuEl) gpuEl.value = load.gpu_layers ?? 99;
    const cpuEl = document.getElementById('llamaSettingsCpuThreads');
    if (cpuEl) cpuEl.value = load.cpu_threads ?? 9;
    const evalEl = document.getElementById('llamaSettingsEvalBatch');
    if (evalEl) evalEl.value = load.eval_batch_size ?? 2048;
    const physEl = document.getElementById('llamaSettingsPhysicalBatch');
    if (physEl) physEl.value = load.physical_batch_size ?? 512;
    const flashEl = document.getElementById('llamaSettingsFlashAttention');
    if (flashEl) flashEl.checked = load.flash_attention !== false;
    const parallelEl = document.getElementById('llamaSettingsParallelSlots');
    if (parallelEl) parallelEl.value = load.parallel_slots ?? 4;
    const tempEl = document.getElementById('llamaSettingsTemperature');
    if (tempEl) tempEl.value = Number(infer.temperature ?? 0.7).toFixed(2);
    const topPEl = document.getElementById('llamaSettingsTopP');
    if (topPEl) topPEl.value = Number(infer.top_p ?? 0.9).toFixed(2);
    const topKEl = document.getElementById('llamaSettingsTopK');
    if (topKEl) topKEl.value = infer.top_k ?? 40;
    const repeatEl = document.getElementById('llamaSettingsRepeatPenalty');
    if (repeatEl) repeatEl.value = Number(infer.repeat_penalty ?? 1.1).toFixed(2);
    const maxTokensEl = document.getElementById('llamaSettingsMaxTokens');
    if (maxTokensEl) maxTokensEl.value = infer.max_tokens ?? 4096;
    const reasoningEl = document.getElementById('llamaSettingsReasoningEffort');
    if (reasoningEl) {
      reasoningEl.value = ['auto', 'none', 'low', 'medium', 'high', 'max'].includes(infer.reasoning_effort)
        ? infer.reasoning_effort
        : 'auto';
    }
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
    const ctxMaxEl = document.getElementById('serverSettingsContextMax');
    if (ctxMaxEl) ctxMaxEl.value = server.context_max || server.context_size || 131072;
    document.getElementById('serverSettingsIdle').value = server.idle_unload_minutes;
    document.getElementById('serverSettingsProfile').value = server.profile;
    const gpuSel = document.getElementById('serverSettingsGpu');
    if (gpuSel) {
      gpuSel.innerHTML = '<option value="auto">Automatic</option>' + gpus.map((g) =>
        `<option value="${g.index}"${String(server.gpu_device) === String(g.index) ? ' selected' : ''}>${escapeHtml(g.display_name || g.name)}</option>`,
      ).join('');
    }
    fillInspectorLoadSettings(server);
    fillLlamaSettingsForm(server);
  }

  function renderAll() {
    syncPendingLoadsFeed();
    const server = activeServer();
    renderToolbar(server);
    renderCards();
    const selectedEntry = selectedLoadedEntry();
    if (!selectedEntry) {
      renderInspectorEmptyState();
      return;
    }

    const { server: selectedServer, row: selectedRow } = selectedEntry;
    const model = modelFromLoadedEntry(selectedServer, selectedRow);
    renderInspectorSelectionState(true);
    fillInspectorInfo(model);
    syncInspectorReasoningVisibility(model);
    if (!model.external && !inspectorDirty && !inspectorPendingReload && !inspectorFilling) {
      fillInspectorLoadSettings(selectedServer);
    }
    inspectorBound = {
      serverId: model.server_id || selectedServer.id || '',
      modelKey: modelKeyFor(model),
      profile: model.profile,
      context_max: model.context_max,
      gpu_layers_max: model.gpu_layers_max,
      external: !!model.external,
    };
    syncInspectorLoadedState(model);
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
  let catalogRefreshInFlight = null;
  let catalogPartial = false;
  let catalogPartialRetryTimer = null;
  let catalogPartialRetryCount = 0;

  async function performCatalogRefresh({ force = false, shouldRender = true } = {}) {
    const gen = ++catalogRefreshGen;
    const modelPick = document.getElementById('serverModelPick');
    const settingsPick = document.getElementById('serverSettingsPick');
    const showLoading = force || !catalogLoaded;

    if (shouldRender && showLoading) {
      setSelectLoading(modelPick, true, 'Loading models…');
      if (settingsPick && !settingsPick.options.length) {
        setSelectLoading(settingsPick, true, 'Loading engines…');
      }
    }

    void captureConsoleBoot();

    try {
      const [profilesData, quickModelsData] = await Promise.all([
        api('/api/servers/profiles'),
        api('/api/models', { timeoutMs: 60000 }),
      ]);
      if (gen !== catalogRefreshGen) return;
      const profileAll = profilesData.all_servers || profilesData.servers || [];
      const profileEnabled = profilesData.servers || [];
      catalogModels = quickModelsData.models || [];
      catalogPartial = quickModelsData.partial === true;
      if (!catalogPartial) catalogPartialRetryCount = 0;
      catalogLoaded = true;
      if (!initialStatusSettled) {
        allServers = profileAll;
        servers = profileEnabled;
      } else {
        const known = new Set(allServers.map((row) => row.id));
        for (const row of profileAll) {
          if (row?.id && !known.has(row.id)) {
            allServers.push(row);
            known.add(row.id);
          }
        }
      }
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

    schedulePartialCatalogRefresh();
  }

  async function refreshCatalog(options = {}) {
    if (catalogRefreshInFlight) return catalogRefreshInFlight;
    catalogRefreshInFlight = performCatalogRefresh(options).finally(() => {
      catalogRefreshInFlight = null;
    });
    return catalogRefreshInFlight;
  }

  function schedulePartialCatalogRefresh() {
    if (catalogPartialRetryTimer) {
      window.clearTimeout(catalogPartialRetryTimer);
      catalogPartialRetryTimer = null;
    }
    if (!catalogPartial) {
      catalogPartialRetryCount = 0;
      return;
    }
    catalogPartialRetryCount += 1;
    const delay = catalogPartialRetryCount > 10 ? 5000 : 900;
    catalogPartialRetryTimer = window.setTimeout(() => {
      catalogPartialRetryTimer = null;
      void refreshCatalog({ shouldRender: true });
    }, delay);
  }

  function mergeInferenceStats(serverId, stats) {
    if (!serverId || !stats || typeof stats !== 'object') return false;
    let changed = false;
    for (const list of [servers, allServers]) {
      const server = list.find((entry) => entry?.id === serverId);
      if (!server) continue;
      server.inference_stats = stats;
      if (Array.isArray(server.visible_cards)) {
        server.visible_cards = server.visible_cards.map((row) => ({
          ...row,
          inference_stats: stats,
        }));
      }
      changed = true;
    }
    return changed;
  }

  let inferenceStatsPollInFlight = false;

  async function refreshInferenceStats(shouldRender = true) {
    if (inferenceStatsPollInFlight) return;
    const targets = servers.filter((server) => (
      server?.id
      && server?.api_url
      && (
        server.status === 'loaded'
        || server?.loaded_models?.length
        || inferenceIsGenerating(server.inference_stats)
      )
    ));
    if (!targets.length) return;
    inferenceStatsPollInFlight = true;
    try {
      const results = await Promise.all(targets.map(async (server) => {
        try {
          const data = await api(`/api/servers/${encodeURIComponent(server.id)}/inference-stats`, {
            timeoutMs: 1500,
          });
          return { serverId: server.id, stats: data?.inference_stats };
        } catch {
          return null;
        }
      }));
      let changed = false;
      for (const result of results) {
        if (result?.stats) changed = mergeInferenceStats(result.serverId, result.stats) || changed;
      }
      if (changed && shouldRender) {
        renderCards();
        updateEnginePageNotice();
      }
    } finally {
      inferenceStatsPollInFlight = false;
    }
  }

  async function refreshStatus(
    shouldRender = true,
    { includeExternal, fresh = false } = {},
  ) {
    const onEngines = enginesViewActive();
    const includeExt = includeExternal ?? onEngines;
    const wantFresh = fresh || (onEngines && enginesNeedFastRefresh());
    engineStatusLoadingDetail = includeExt
      ? 'Building engine status and scanning external GPU apps…'
      : 'Checking configured llama-server listeners and loaded models…';
    updateEnginePageNotice();
    try {
      const data = await api(serversStatusUrl(includeExt, wantFresh), {
        timeoutMs: wantFresh ? 0 : (includeExt ? 20000 : undefined),
      });
      if (!applyServersPayload(data, { mergeExternal: includeExt })) return;
      if (includeExt) {
        externalScanError = String(data?.external_scan_error || '').trim();
      }
      initialStatusSettled = true;
      engineStatusLoadingDetail = '';
      syncActiveIdFromLiveState();
      if (shouldRender) {
        renderAll();
        updateEnginePageNotice();
        if (!hasPendingEngineActions() && !anyServerGenerating()) await refreshLogs();
      }
    } catch {
      /* keep last known state */
    }
  }

  async function refresh(shouldRender = true, options = {}) {
    void ensureTotalVram();
    const onEngines = enginesViewActive();
    const merged = {
      includeExternal: options.includeExternal ?? false,
      fresh: options.fresh ?? (onEngines && enginesNeedFastRefresh()),
      ...options,
    };
    if (!catalogLoaded) {
      void refreshCatalog({ shouldRender });
    }
    await refreshStatus(shouldRender, merged);
    reschedulePoll();
  }

  function sameExternalModel(entry, pid, modelName, appLabel) {
    if (Number(entry?.pid) === Number(pid)) return true;
    const name = String(modelName || '').trim().toLowerCase();
    const app = String(appLabel || '').trim().toLowerCase();
    if (!name || !app) return false;
    return String(entry?.model_name || entry?.title || '').trim().toLowerCase() === name
      && String(entry?.app_label || '').trim().toLowerCase() === app;
  }

  async function waitUntilExternalUnloaded(pid, { modelName = '', appLabel = '' } = {}) {
    const numericPid = Number(pid);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await refreshExternalGpuLoads(true, { force: true, fresh: true });
      if (!externalGpuLoads.some((entry) => sameExternalModel(entry, numericPid, modelName, appLabel))) {
        return true;
      }
      if (attempt === 0) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
    }
    return false;
  }

  async function refreshAfterUnload() {
    await refreshExternalGpuLoads(true, { force: true, fresh: true });
    void refreshStatus(true, { includeExternal: true, fresh: true }).catch(() => {});
    reschedulePoll();
  }

  async function ensureTotalVram() {
    // Total VRAM across all GPUs, cached so the engine card VRAM% badge has a
    // denominator.  Refetch on each refresh is avoided; a null stays null.
    if (totalVramGb != null) return;
    try {
      const data = await api('/api/system-stats', { timeoutMs: 8000 });
      const gpusData = data?.gpus || [];
      const total = gpusData.reduce((sum, g) => sum + (Number(g.vram_total_gb ?? g.vram_gb) || 0), 0);
      if (total > 0) totalVramGb = total;
    } catch (_err) {
      /* keep null */
    }
  }

  async function pollTick() {
    if (pollInFlight) return;
    pollInFlight = true;
    const view = document.body.dataset.activeView;
    const onEngines = view === 'server';
    externalPollCounter += 1;
    try {
      await refresh(onEngines, {
        // Keep the high-frequency status loop off the expensive external GPU
        // scan. External cards are refreshed independently below.
        includeExternal: false,
        fresh: enginesNeedFastRefresh(),
      });
      if (onEngines) {
        void refreshInferenceStats(true);
        void refreshExternalGpuLoads(true);
      }
    } finally {
      pollInFlight = false;
    }
    if (view === 'models' && window.DFlashModelsLive) {
      try {
        await window.DFlashModelsLive.refresh();
      } catch {
        /* ignore */
      }
    }
  }

  async function waitUntilModelLoaded(serverId, { maxAttempts = 180, onProgress } = {}) {
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await refresh(true);
      const server = servers.find((s) => s.id === serverId);
      onProgress?.(server);
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
      await new Promise((resolve) => window.setTimeout(resolve, 300));
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

  function dflashRepairDetail(error) {
    const detail = error?.apiDetail;
    if (detail?.repair) return detail;
    if (detail?.detail?.repair) return detail.detail;
    return null;
  }

  async function openDflashRepair(error, model, serverId) {
    const detail = dflashRepairDetail(error);
    if (!detail?.repair) return false;
    const repair = detail.repair;
    const action = String(repair.action || '').toLowerCase();
    if (action === 'update_engine') {
      const message = detail.message || detail.update?.engine?.message || 'Update the bundled llama-server before loading this DFlash2 stack.';
      toast(message, false);
      window.DFlashStatusFeed?.note('DFlash engine update required', message);
      return true;
    }
    const targetPath = repair.target_path || detail.target_path || model?.path || '';
    const targetLabel = model?.label || model?.filename || targetPath.split(/[/\\]/).pop() || 'DFlash target';
    const currentDraftPath = repair.current_draft_path || '';
    const retry = async (attached) => {
      await executeModelLoad(
        model,
        serverId || attached?.server_id || attached?.server?.id,
        { skipLoadPlanCheck: true },
      );
    };
    if (serverId && targetPath && window.DFlashStackWizard?.openReplaceDraft) {
      await window.DFlashStackWizard.openReplaceDraft({
        serverId,
        targetPath,
        targetLabel,
        currentDraftPath,
        currentDraftLabel: currentDraftPath.split(/[/\\]/).pop() || '',
        label: targetLabel,
        onAttached: retry,
      });
      window.DFlashStatusFeed?.note('DFlash draft required', 'Choose or download the matching accelerator');
      return true;
    }
    if (targetPath && window.DFlashStackWizard?.open) {
      await window.DFlashStackWizard.open({
        targetPath,
        targetLabel,
        allowHfAccelerator: true,
        onAttached: retry,
      });
      window.DFlashStatusFeed?.note('DFlash draft required', 'Choose or download the matching accelerator');
      return true;
    }
    toast(detail.message || 'Choose a target and matching DFlash accelerator in the stack wizard.', false);
    return true;
  }

  async function executeModelLoad(model, forceServerId, options = {}) {
    const onProgress = options.onProgress;
    const serverId = forceServerId || model.server_id || activeServer()?.id;
    if (!serverId) {
      toast('Select an engine first', false);
      return;
    }
    const label = model.label || model.id;
    const liveServer = findLiveServerForModel(model);
    if (liveServer) {
      if (liveServer.acceleration_expected && liveServer.draft_loaded !== true) {
        await openDflashRepair({
          apiDetail: {
            message: 'This DFlash profile is loaded without a verifiable draft accelerator.',
            repair: {
              action: 'attach_draft',
              server_id: serverId,
              target_path: model.path || liveServer.target_path || '',
              current_draft_path: '',
            },
          },
        }, model, serverId);
        return false;
      }
      toast('Model already loaded');
      window.DFlashStatusFeed?.note(`${label} ready`, `Port :${liveServer.port || '—'}`);
      return true;
    }
    setServerAction(serverId, 'loading');
    pendingLoads.set(serverId, { label, plain_gguf: !!model.plain_gguf });
    syncPendingLoadsFeed();
    window.DFlashStatusFeed?.setTransient(`Loading ${label}…`, {
      secondary: 'Reading target and draft weights into GPU',
      ttlMs: 120000,
    });
    renderAll();
    let completed = false;
    try {
      await saveInspectorLoadSettings();
      const body = {};
      if (shouldSendModelPath(model, serverId)) {
        body.model_path = model.path;
        const loadId = catalogLoadModelId(model);
        if (loadId) body.model_id = loadId;
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
        completed = true;
      } else if (result?.loaded) {
        const loaded = await waitUntilModelLoaded(serverId, { onProgress });
        if (loaded?.status === 'loaded' || loaded?.loaded_models?.length) {
          toast('Model loaded');
          window.DFlashStatusFeed?.note(`${label} ready`, `Port :${loaded?.port || result.port || '—'}`);
          clearInspectorPendingReload();
          completed = true;
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
      } else {
        const message = result?.error
          || 'Model load did not complete. Check the engine log and try again.';
        toast(message, false);
        window.DFlashStatusFeed?.note('Load did not complete', message);
      }
    } catch (err) {
      if (await openDflashRepair(err, model, serverId)) {
        completed = false;
      } else {
        toast(err.message || 'Model load failed', false);
        window.DFlashStatusFeed?.note('Load failed', err.message || label);
      }
    } finally {
      pendingLoads.delete(serverId);
      syncPendingLoadsFeed();
      setServerAction(serverId, null);
      resetEngineModelPicker();
      renderAll();
      void refreshExternalGpuLoads(true);
      await refreshStatus(true, { includeExternal: true, fresh: false });
      void refreshStatus(true, { includeExternal: true, fresh: true }).catch(() => {});
      reschedulePoll();
    }
    return completed;
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
    return executeModelLoad({ ...model, server_id: model.server_id || '' });
  }

  async function ejectExternalLoad(pid) {
    if (!pid || Number.isNaN(pid)) return;
    const key = `external-${pid}`;
    if (isServerBusy(key)) return;
    const row = externalGpuLoads.find((entry) => Number(entry.pid) === Number(pid));
    const label = row?.title || row?.app_label || `PID ${pid}`;
    const modelName = row?.model_name || row?.title || '';
    const appLabel = row?.app_label || '';
    setServerAction(key, 'ejecting');
    window.DFlashStatusFeed?.setTransient(`Stopping ${label}…`, {
      secondary: row?.app_label ? `External · ${row.app_label}` : 'External GPU process',
      ttlMs: 120000,
    });
    externalGpuLoads = externalGpuLoads.filter((entry) => Number(entry.pid) !== Number(pid));
    suppressExternalEmptyDebounce = true;
    renderAll();
    try {
      const body = {};
      if (row?.api_url) body.api_url = row.api_url;
      if (row?.model_id) body.model_id = row.model_id;
      await api(`/api/gpu/processes/${encodeURIComponent(pid)}/unload`, {
        method: 'POST',
        body: Object.keys(body).length ? JSON.stringify(body) : undefined,
        timeoutMs: 0,
      });
      const removed = await waitUntilExternalUnloaded(pid, { modelName, appLabel });
      if (removed) {
        if (selectedLoadedKey === key) clearLoadedCardSelection();
        toast('External model unloaded');
      } else if (externalGpuLoads.some((entry) => sameExternalModel(entry, pid, modelName, appLabel))) {
        toast('Unload sent — the other app loaded it again', true);
      } else {
        toast('Unload sent — still releasing GPU memory', true);
      }
    } catch (err) {
      toast(err.message, false);
    } finally {
      suppressExternalEmptyDebounce = false;
      setServerAction(key, null);
      renderAll();
      void refreshAfterUnload();
    }
  }

  async function ejectServer(serverId) {
    if (!serverId || isServerBusy(serverId)) return;
    window.DFlashSelectTheme?.closeAllMenus?.();
    const primary = servers.find((s) => s.id === serverId) || allServers.find((s) => s.id === serverId);
    const cards = primary ? loadedRowsForServer(primary) : [];
    const targetPath = entryTargetPath(primary, cards[0] || {});
    const duplicateIds = serversSharingTarget(targetPath).filter((id) => id !== serverId);
    const unloadIds = [serverId, ...duplicateIds];
    setServerAction(serverId, 'ejecting');
    const label = allServers.find((s) => s.id === serverId)?.label || serverId;
    window.DFlashStatusFeed?.setTransient(`Unloading ${label}…`, { ttlMs: 30000 });
    renderAll();
    let unloaded = false;
    try {
      for (const id of unloadIds) {
        setServerAction(id, 'ejecting');
        await api(`/api/servers/${encodeURIComponent(id)}/unload`, { method: 'POST', timeoutMs: 0 });
        await waitUntilServerIdle(id);
        setServerAction(id, null);
      }
      unloaded = true;
      toast(duplicateIds.length ? 'Duplicate model copies unloaded' : 'Model unloaded');
      activeId = serverId;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
      await refreshLogs();
    } catch (err) {
      toast(err.message, false);
    } finally {
      unloadIds.forEach((id) => setServerAction(id, null));
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
      context_max: parseInt(document.getElementById('serverSettingsContextMax')?.value || '131072', 10),
      idle_unload_minutes: parseInt(document.getElementById('serverSettingsIdle').value, 10),
      gpu_device: document.getElementById('serverSettingsGpu').value,
      profile: document.getElementById('serverSettingsProfile').value,
      ...readLlamaSettingsFromForm(),
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
    if (!inferenceStatsTimer) {
      inferenceStatsTimer = window.setInterval(() => {
        if (!enginesViewActive()) return;
        void refreshInferenceStats(true);
      }, LIVE_STATS_INTERVAL_MS);
    }
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      if (!enginesViewActive()) return;
      // Wake up with the local snapshot first. A fresh external GPU scan here
      // can hold the shared status lock and make a newly opened window look
      // stuck for tens of seconds.
      void refresh(true, { includeExternal: false, fresh: false });
    });
  }

  function bind() {
    window.DFlashRuntimeSteppers?.bindInspectorSteppers?.();

    const autoSaveIds = [
      'inspectorContext', 'inspectorContextMax', 'inspectorGpuLayers', 'inspectorCpuThreads', 'inspectorEvalBatch',
      'inspectorPhysicalBatch', 'inspectorFlashAttention', 'inspectorParallelSlots', 'inspectorTemperature', 'inspectorTopP',
      'inspectorTopK', 'inspectorRepeatPenalty', 'inspectorMaxTokens', 'inspectorReasoningEffort',
    ];
    autoSaveIds.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      const isSelect = el.tagName === 'SELECT';
      const eventName = (el.type === 'checkbox' || isSelect) ? 'change' : 'input';
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
    window.addEventListener('dflash-load-engine', () => {
      renderEngineModelPicker();
      if (!catalogLoaded || catalogModels.length < 8) {
        void refreshCatalog({ force: false, shouldRender: true });
      }
    });
    document.getElementById('serverSourcePick')?.addEventListener('change', () => {
      clearLoadedCardSelection();
      renderInspectorEmptyState();
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
    document.getElementById('engineCardsRefreshBtn')?.addEventListener('click', () => {
      void manualRefreshEngineCards();
    });

    document.getElementById('serverSettingsPick')?.addEventListener('change', (e) => {
      clearLoadedCardSelection();
      renderInspectorEmptyState();
      activeId = e.target.value;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
      const server = allServers.find((s) => s.id === activeId) || activeServer();
      fillSettingsForm(server);
      const llamaPick = document.getElementById('llamaSettingsPick');
      if (llamaPick) llamaPick.value = activeId;
      void refresh();
    });

    document.getElementById('llamaSettingsPick')?.addEventListener('change', (e) => {
      activeId = e.target.value;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
      const server = allServers.find((s) => s.id === activeId) || activeServer();
      fillSettingsForm(server);
      const enginePick = document.getElementById('serverSettingsPick');
      if (enginePick) enginePick.value = activeId;
      void refresh();
    });

    document.addEventListener('click', hideCardContextMenu);
    document.addEventListener('scroll', hideCardContextMenu, true);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideCardContextMenu();
    });
  }

  async function loadModelOnServer(serverId, model, options = {}) {
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
    if (!options.skipLoadPlanCheck) {
      const plan = await fetchLoadPlan(payload, serverId);
      if (plan?.level === 'already_loaded') {
        toast(plan.message || 'Model already loaded');
        const label = payload.label || payload.id || 'Model';
        window.DFlashStatusFeed?.note(`${label} ready`, plan.port ? `Port :${plan.port}` : 'ready');
        return true;
      }
      if (plan?.level === 'repair_required') {
        await openDflashRepair({ apiDetail: plan }, payload, serverId);
        return false;
      }
      if (plan?.level === 'block') {
        toast(plan.message || 'This model does not fit the current GPU memory.', false);
        return false;
      }
    }
    return executeModelLoad(payload, serverId, options);
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    updateEnginePageNotice();
    void loadGatewayUrl();
    void initEngineFilters()
      .then(() => refreshStatus(true, { includeExternal: false, fresh: false }))
      .then(() => {
        startPolling();
        void refreshExternalGpuLoads(true, { force: true });
        void refreshCatalog({ shouldRender: true });
      })
      .catch((err) => toast(err.message, false));
  });

  window.DFlashServerLive = {
    refresh,
    manualRefreshEngineCards,
    reschedulePoll,
    startActive,
    ejectActive,
    stopActive,
    activeServer,
    applyModelSelection,
    loadSelectedModel,
    loadModelOnServer,
    checkLoadPlan: fetchLoadPlan,
    fillSettingsForm,
    fillLlamaSettingsForm,
    saveGatewaySettings,
    fillInspectorLoadSettings,
    flushInspectorSave,
    getMergedLoadSettings,
    modelKeyFor,
    syncModelPicker,
    resetEngineModelPicker,
    rememberInspectorTab,
    focusInspectorTab,
    ensureInspectorVisible,
    refreshCatalog,
  };
})();
