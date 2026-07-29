/** Live Server tab — polls /api/servers and renders model stack cards */
(function () {
  const { api, toast } = window.ConsoleApi;

  const SPEC_PROFILES = new Set(['gemma-chat', 'gemma-12-dflash', 'qwen-dflash', 'bonsai-spec']);
  const PROFILE_CTX_MAX = {
    'gemma-chat': 262144,
    'gemma-ar': 262144,
    'gemma-12-dflash': 262144,
    'qwen-dflash': 32768,
    'qwen-ar': 32768,
    'bonsai': 8192,
    'bonsai-spec': 16384,
  };

  let servers = [];
  let allServers = [];
  let gpus = [];
  let activeId = localStorage.getItem('dflashConsole.activeServerId') || '';
  let pollTimer = null;
  const serverActions = new Map();
  const pendingLoads = new Map();
  const PREFS_KEY = 'dflashConsole.modelPrefs';
  let catalogModels = [];
  let suppressRunningToggle = false;
  let selectedModelKey = localStorage.getItem('dflashConsole.selectedModelKey') || '';

  const MODEL_GROUPS = [
    { id: 'profiles', label: 'DFlash engine profiles', match: (m) => m.source === 'dflash-profile' },
    {
      id: 'dflash',
      label: 'DFlash models',
      match: (m) => m.source !== 'dflash-profile' && (
        m.source === 'dflash'
        || (Array.isArray(m.capabilities) && m.capabilities.includes('dflash'))
        || !!m.draft_path
      ),
    },
    { id: 'lmstudio', label: 'LM Studio', match: (m) => m.source === 'lmstudio' },
    { id: 'gguf', label: 'GGUF library', match: () => true },
  ];
  let inspectorBound = null;
  let inspectorFilling = false;
  let inspectorDirty = false;
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
      role: 'alias',
      ejectable: true,
      progress: null,
    };
  }

  function activeServer() {
    return servers.find((s) => s.id === activeId) || allServers.find((s) => s.id === activeId) || servers[0] || null;
  }

  function serverIsLive(server) {
    return !!server && (server.running || server.status === 'booting' || server.status === 'loaded');
  }

  /** Follow backend when another profile was started via API while the UI had a stopped selection. */
  function syncActiveIdFromLiveState() {
    if (isServerBusy(activeId)) return;
    if (serverIsLive(activeServer())) return;
    const live = servers.find((s) => serverIsLive(s));
    if (live && live.id !== activeId) {
      activeId = live.id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
  }

  function pollIntervalMs() {
    if (serverActions.size > 0 || anyServerLoading() || loadedServerCount() > 0) return 1000;
    return 2500;
  }

  function reschedulePoll() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = window.setInterval(() => void pollTick(), pollIntervalMs());
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
    window.DFlashStatusFeed?.note('Runtime settings saved', 'Changes apply on next load');
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

  function modelFromLoadedEntry(server, row) {
    const catalog = catalogModelForServer(server.id);
    if (catalog) return catalog;
    return {
      id: server.id,
      server_id: server.id,
      label: cardDisplayName(row) || server.label || server.id,
      profile: server.profile || '',
      path: row.path || '',
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
    const bodyEl = document.querySelector('.lm-body');
    const inspectorToggleBtn = document.querySelector('[data-action="toggle-inspector"]');
    if (!bodyEl?.classList.contains('inspector-collapsed')) return;
    bodyEl.classList.remove('inspector-collapsed');
    inspectorToggleBtn?.classList.add('active');
  }

  function focusInspectorTab(tabId) {
    const tab = document.querySelector(`.lm-inspector-tab[data-inspector-tab="${tabId}"]`);
    if (!tab) return;
    tab.click();
  }

  async function selectLoadedCard(server, row, { tab } = {}) {
    if (!server) return;
    activeId = server.id;
    localStorage.setItem('dflashConsole.activeServerId', activeId);
    const model = modelFromLoadedEntry(server, row);
    selectedModelKey = modelCatalogKey(model);
    if (selectedModelKey) {
      localStorage.setItem('dflashConsole.selectedModelKey', selectedModelKey);
    }
    syncModelPicker(selectedModelKey);
    await applyModelSelection(model);
    ensureInspectorVisible();
    if (tab) focusInspectorTab(tab);
    renderCards();
    renderToolbar(activeServer());
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

    menu.innerHTML = `
      <button type="button" data-cmd="details">Show details</button>
      <button type="button" data-cmd="runtime">Show runtime settings</button>
      <button type="button" data-cmd="copy-url"${url ? '' : ' disabled'}>Copy API URL</button>
      <button type="button" data-cmd="copy-path"${path ? '' : ' disabled'}>Copy model path</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <hr>
      <button type="button" data-cmd="unload"${ready && row.ejectable ? '' : ' disabled'}>Unload</button>
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
      await ejectServer(server.id);
      return;
    }
    if (cmd === 'cancel') {
      await stopServer(server.id);
    }
  }

  function loadedServerCount() {
    return servers.filter((s) => s.status === 'loaded').length;
  }

  function entryForCard(card) {
    const serverId = card?.dataset?.serverId;
    const role = card?.dataset?.role;
    const server = servers.find((s) => s.id === serverId);
    if (!server) return null;
    let row = server.visible_cards?.find((entry) => entry.role === role);
    if (!row) row = pendingLoadRow(serverId);
    return row ? { server, row } : null;
  }

  function bootingServerCount() {
    return servers.filter((s) => s.status === 'booting').length;
  }

  function collectLoadedEntries() {
    const entries = [];
    for (const server of servers) {
      const cards = Array.isArray(server.visible_cards) ? server.visible_cards : [];
      for (const row of cards) {
        entries.push({ server, row });
      }
    }
    for (const [serverId] of pendingLoads) {
      if (entries.some(({ server }) => server.id === serverId)) continue;
      const server = servers.find((s) => s.id === serverId);
      const row = pendingLoadRow(serverId);
      if (server && row) entries.push({ server, row });
    }
    return entries;
  }

  function serverStatusLabel(server) {
    if (!server) return 'Stopped';
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
    if (loading === 1 || booting === 1) return 'Loading model…';
    if (loaded > 1) return `${loaded} models loaded`;
    if (loaded === 1) return 'Running';
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

  function cardDisplayName(row) {
    if (row.title) return row.title;
    if (row.role === 'alias') return row.id || 'API alias';
    if (row.role === 'draft-dflash' || row.role === 'draft-dspark') {
      const base = row.path ? row.path.split(/[/\\]/).pop() : row.label;
      return base || row.id || 'draft';
    }
    const base = row.path ? row.path.split(/[/\\]/).pop() : row.label;
    return base || row.id || 'model';
  }

  function cardHoverTitle({ server, row }) {
    const lines = [
      `${server.label || server.id} · port :${server.port}`,
      server.reachable_url || '',
      row.subtitle || '',
    ];
    const details = Array.isArray(row.stack_details) ? row.stack_details : [];
    for (const part of details) {
      const size = part.size_gb != null ? ` · ${part.size_gb} GB` : '';
      lines.push(`${detailBadge(part.source, part.role)}: ${part.name || '—'}${size}`);
    }
    if (row.path) lines.push(row.path);
    return lines.filter(Boolean).join('\n');
  }

  function roleBadge(row) {
    if (row.role === 'draft-dflash') return '<span class="lm-tag green">DFlash draft</span>';
    if (row.role === 'draft-dspark') return '<span class="lm-tag yellow">dspark draft</span>';
    if (row.role === 'alias') return '<span class="lm-tag blue">API</span>';
    if (row.source === 'lmstudio') return '<span class="lm-tag blue">LM Studio</span>';
    return '';
  }

  function cardLiveStats(server, ready) {
    if (!ready || server?.status !== 'loaded') return '';
    const stats = server?.inference_stats || {};
    if (stats.generating) {
      const secs = stats.generating_seconds != null ? `${stats.generating_seconds}s` : '…';
      return `
      <div class="lm-model-card-live-row is-generating" title="Inference running on this engine">
        <span class="lm-model-card-live-metric"><span class="lm-model-card-live-label">Status</span> Generating ${escapeHtml(secs)}</span>
        <span class="lm-model-card-live-metric dim">Live</span>
      </div>`;
    }
    const out = stats.generation_tokens;
    const tps = stats.tokens_per_second;
    const outText = out != null ? `${out} tok` : '— tok';
    const tpsText = tps != null ? `${tps} t/s` : '— t/s';
    const tip = out != null || tps != null
      ? 'Last completion on this engine'
      : 'Run inference to measure output tokens and speed';
    return `
      <div class="lm-model-card-live-row" title="${escapeHtml(tip)}">
        <span class="lm-model-card-live-metric"><span class="lm-model-card-live-label">Generated</span> ${escapeHtml(outText)}</span>
        <span class="lm-model-card-live-metric"><span class="lm-model-card-live-label">Speed</span> ${escapeHtml(tpsText)}</span>
      </div>`;
  }

  function emptyMessage(server) {
    const action = getServerAction(server?.id);
    if (action === 'stopping') return 'Stopping server…';
    if (action === 'ejecting') return 'Unloading model…';
    if (action === 'starting') return 'Starting engine…';
    if (action === 'loading' || server?.status === 'booting') return 'Loading model…';
    if (server?.status === 'running') return 'Engine is listening but no model is loaded. Click Load.';
    return 'Engine stopped. Turn it on or load a model.';
  }

  function renderCards() {
    const wrap = document.getElementById('serverModelCards');
    const empty = document.getElementById('serverEmptyState');
    if (!wrap || !empty) return;

    const entries = collectLoadedEntries();
    if (!entries.length) {
      wrap.innerHTML = '';
      empty.textContent = emptyMessage(activeServer());
      empty.classList.remove('hidden');
      return;
    }
    empty.classList.add('hidden');

    wrap.innerHTML = entries.map(({ server, row }) => {
      const ready = row.card_state === 'ready';
      const loading = row.card_state === 'loading';
      const rawProgress = row.progress ?? (loading ? server.load_progress : null);
      const progressPct = rawProgress != null ? Math.min(100, Math.max(0, Number(rawProgress))) : null;
      let action = '';
      if (row.ejectable) {
        action = ready
          ? '<button class="lm-btn ghost small" data-action="eject" title="Unload model">Unload</button>'
          : '<button class="lm-btn ghost small" data-action="cancel-load">Cancel</button>';
      }
      const isSelected = server.id === activeId;
      const cardClass = `lm-model-card lm-model-card-compact ${ready ? 'ready' : 'loading'}${isSelected ? ' selected' : ''}`;
      const cardStyle = loading && progressPct != null ? ` style="--card-progress:${progressPct}%"` : '';
      const loadChrome = loading
        ? `<div class="lm-model-card-load-shell" aria-hidden="true">
            <span class="lm-model-card-load-label">Loading<span class="lm-loading-dots"><span>.</span><span>.</span><span>.</span></span></span>
            <div class="lm-model-card-load-track"><div class="lm-model-card-load-fill"></div></div>
          </div>`
        : '';
      const badge = ready
        ? '<span class="lm-badge ready">READY</span>'
        : `<span class="lm-badge loading">${progressPct != null ? `${Math.round(progressPct)}%` : '…'}</span>`;
      const missing = row.path_missing ? '<span class="lm-tag yellow">missing</span>' : '';
      const hoverTitle = cardHoverTitle({ server, row });
      const engineMeta = escapeHtml(server.label || server.id);

      return `
        <article class="${cardClass}" data-server-id="${escapeHtml(server.id)}" data-role="${escapeHtml(row.role)}" role="button" tabindex="0" title="${escapeHtml(hoverTitle)}"${cardStyle}>
          ${loadChrome}
          ${cardLiveStats(server, ready)}
          <div class="lm-model-card-top">
            ${badge}
            <span class="lm-model-path">${escapeHtml(cardDisplayName(row))}</span>
            <span class="lm-model-card-meta"><span class="lm-port">:${server.port}</span> · ${engineMeta}</span>
            ${roleBadge(row)} ${missing}
            <div class="lm-model-stats">
              ${action}
            </div>
          </div>
        </article>`;
    }).join('');

    wrap.querySelectorAll('[data-action="eject"]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const card = e.target.closest('[data-server-id]');
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
  }

  function modelCatalogKey(model) {
    return model?.server_id || model?.path || model?.id || '';
  }

  function modelGroupId(model) {
    for (const group of MODEL_GROUPS) {
      if (group.id === 'gguf') continue;
      if (group.match(model)) return group.id;
    }
    return 'gguf';
  }

  function modelOptionLabel(model) {
    const parts = [model.label || model.filename || model.id || 'Model'];
    if (model.quant && model.quant !== '—') parts.push(model.quant);
    if (model.size_gb != null) parts.push(`${model.size_gb} GB`);
    if (model.loadable && model.port) parts.push(`port :${model.port}`);
    else if (model.path && !model.server_id) parts.push('load on active engine');
    return parts.join(' · ');
  }

  function groupedCatalogModels(list) {
    const buckets = Object.fromEntries(MODEL_GROUPS.map((g) => [g.id, []]));
    const seen = new Set();
    for (const model of list) {
      const key = modelCatalogKey(model);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      buckets[modelGroupId(model)].push(model);
    }
    for (const group of MODEL_GROUPS) {
      buckets[group.id].sort((a, b) => {
        const aScore = a.loadable ? 0 : 1;
        const bScore = b.loadable ? 0 : 1;
        if (aScore !== bScore) return aScore - bScore;
        return String(a.label || '').localeCompare(String(b.label || ''));
      });
    }
    return buckets;
  }

  function renderEngineModelPicker() {
    const pick = document.getElementById('serverModelPick');
    const loadBtn = document.getElementById('serverModelLoadBtn');
    if (!pick) return;

    const buckets = groupedCatalogModels(catalogModels);
    const parts = ['<option value="">Select model…</option>'];
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

    const selected = catalogModels.find((m) => modelCatalogKey(m) === pick.value);
    if (loadBtn) loadBtn.disabled = !canLoadModel(selected);
  }

  function syncModelPicker(key) {
    selectedModelKey = key || localStorage.getItem('dflashConsole.selectedModelKey') || '';
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
    if (selectedModelKey) localStorage.setItem('dflashConsole.selectedModelKey', selectedModelKey);
    else localStorage.removeItem('dflashConsole.selectedModelKey');
    if (loadBtn) loadBtn.disabled = !canLoadModel(model);
    if (model?.server_id) {
      activeId = model.server_id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
    if (model) {
      await applyModelSelection(model);
      await window.DFlashModelsLive?.selectModel?.(selectedModelKey, { applyInspector: false });
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
      const anyActive = loadedServerCount() > 0 || bootingServerCount() > 0 || server.running || server.status === 'booting';
      statusText.className = anyActive ? 'lm-status-running' : 'lm-status-stopped';
    }
    if (toggle) setRunningToggle(running && getServerAction(server.id) !== 'stopping');
    if (urlEl) urlEl.textContent = server.reachable_url || '—';
    if (loadBtn) {
      const picked = selectedCatalogModel();
      loadBtn.disabled = !canLoadModel(picked);
    }
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
    if (specGroup) specGroup.classList.toggle('hidden', !SPEC_PROFILES.has(server.profile));
    if (specHint) {
      if (server.profile === 'gemma-chat' || server.profile === 'qwen-dflash' || server.profile === 'gemma-12-dflash') {
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
    document.getElementById('inspectorInfoContext').textContent = `${model.context_max || 131072} tokens`;
    document.getElementById('inspectorInfoPath').textContent = model.path || model.id || '—';
    document.getElementById('inspectorInfoProfile').textContent = model.profile || '—';
    const caps = document.getElementById('inspectorInfoCaps');
    if (caps) {
      const tags = [];
      const list = model.capabilities || [];
      if (list.includes('tools')) tags.push('<span class="lm-tag green">tools</span>');
      if (list.includes('ar')) tags.push('<span class="lm-tag blue">AR</span>');
      if (list.includes('dflash')) tags.push('<span class="lm-tag green">dflash</span>');
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
    document.getElementById('inspectorHeadTitle')?.replaceChildren(document.createTextNode(model.label || model.id || 'Model'));
  }

  async function applyModelSelection(model) {
    if (!model) return;
    await flushInspectorSave();
    inspectorDirty = false;
    inspectorBound = {
      serverId: model.server_id || '',
      modelKey: modelKeyFor(model),
      profile: model.profile,
      context_max: model.context_max,
      gpu_layers_max: model.gpu_layers_max,
    };
    fillInspectorInfo(model);
    fillInspectorLoadSettings(getMergedLoadSettings(model));
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
    const server = activeServer();
    renderToolbar(server);
    renderCards();
    if (!server) return;

    if (inspectorBound?.serverId === server.id) {
      if (!inspectorDirty && !inspectorFilling) {
        fillInspectorLoadSettings(server);
      }
      return;
    }

    if (document.body.dataset.activeView !== 'server') return;

    const model = catalogModelForServer(server.id) || modelFromLoadedEntry(server, server.visible_cards?.[0] || {});
    fillInspectorInfo(model);
    if (!inspectorDirty && !inspectorFilling) {
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

  async function refresh(shouldRender = true) {
    const [data, modelsData] = await Promise.all([
      api('/api/servers'),
      api('/api/models').catch(() => ({ models: [] })),
    ]);
    servers = data.servers || [];
    allServers = data.all_servers || servers;
    gpus = data.gpus || [];
    catalogModels = modelsData.models || [];
    selectedModelKey = localStorage.getItem('dflashConsole.selectedModelKey') || selectedModelKey;
    if (!activeId || !allServers.some((s) => s.id === activeId)) {
      activeId = data.primary_server_id || servers[0]?.id || allServers[0]?.id || '';
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
    syncActiveIdFromLiveState();
    if (shouldRender) {
      renderAll();
      await refreshLogs();
    }
    reschedulePoll();
  }

  async function pollTick() {
    const view = document.body.dataset.activeView;
    await refresh(view === 'server');
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
      if (server && !server.booting && server.status !== 'booting' && attempt > 2) {
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
    window.DFlashStatusFeed?.setTransient(`Starting engine ${server.label || server.id}…`, {
      secondary: `Port :${server.port}`,
      ttlMs: 120000,
    });
    renderAll();
    try {
      await api(`/api/servers/${encodeURIComponent(server.id)}/listen`, { method: 'POST' });
      toast('Engine started');
      window.DFlashStatusFeed?.note('Engine listening', `Port :${server.port} · no model loaded yet`);
      await refresh();
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
    pendingLoads.set(serverId, { label });
    window.DFlashStatusFeed?.setTransient(`Loading ${label}…`, {
      secondary: 'Reading weights into GPU',
      ttlMs: 120000,
    });
    renderAll();
    try {
      await saveInspectorLoadSettings();
      const body = {};
      if (model.path && !model.server_id) {
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
        await refresh();
        return;
      }
      const loaded = await waitUntilModelLoaded(serverId);
      if (loaded?.status === 'loaded') {
        toast('Model loaded');
        window.DFlashStatusFeed?.note(`${label} ready`, `Port :${loaded.port || '—'}`);
      }
    } catch (err) {
      toast(err.message, false);
      window.DFlashStatusFeed?.note('Load failed', err.message || label);
    } finally {
      pendingLoads.delete(serverId);
      setServerAction(serverId, null);
      renderAll();
      await refresh();
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

  async function ejectServer(serverId) {
    if (!serverId || isServerBusy(serverId)) return;
    setServerAction(serverId, 'ejecting');
    const label = allServers.find((s) => s.id === serverId)?.label || serverId;
    window.DFlashStatusFeed?.setTransient(`Unloading ${label}…`, { ttlMs: 30000 });
    renderAll();
    try {
      await api(`/api/servers/${encodeURIComponent(serverId)}/unload`, { method: 'POST' });
      toast('Model unloaded — server still running');
      await waitUntilServerIdle(serverId);
      activeId = serverId;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
      await refreshLogs();
    } catch (err) {
      toast(err.message, false);
    } finally {
      setServerAction(serverId, null);
      await refresh();
    }
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
      await refresh();
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
    document.getElementById('serverModelPick')?.addEventListener('change', () => {
      void onEngineModelPickChange();
    });
    document.getElementById('serverCopyUrl')?.addEventListener('click', () => {
      const url = document.getElementById('serverReachableUrl')?.textContent;
      if (url && url !== '—') navigator.clipboard.writeText(url).then(() => toast('URL copied'));
    });
    document.getElementById('serverLogsRefresh')?.addEventListener('click', () => void refreshLogs().catch((e) => toast(e.message, false)));
    document.getElementById('serverLogsCopy')?.addEventListener('click', () => void copyVisibleLogs());
    document.getElementById('serverLogsClear')?.addEventListener('click', () => void clearLogs());
    bindLogsFilterDropdown();

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
    if (payload.server_id !== serverId) payload.server_id = '';
    if (!payload.server_id && !payload.path) {
      toast('This model cannot be loaded', false);
      return false;
    }
    await executeModelLoad(payload, serverId);
    return true;
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    void refresh().then(startPolling).catch((err) => toast(err.message, false));
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
  };
})();
