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
  let busy = false;
  let busyAction = null;
  let loadingModelMeta = null;
  const PREFS_KEY = 'dflashConsole.modelPrefs';
  let catalogModels = [];
  let suppressRunningToggle = false;
  let selectedModelKey = localStorage.getItem('dflashConsole.selectedModelKey') || '';

  const MODEL_GROUPS = [
    { id: 'profiles', label: 'DFlash engine profiles', match: (m) => m.source === 'dflash-profile' },
    {
      id: 'dflash',
      label: 'DFlash checkpoints',
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
  let lastLogsServerId = '';
  let logLinesRaw = [];
  let logFilterId = localStorage.getItem('dflashConsole.logFilter') || 'all';
  const LOG_FETCH_TAIL = 500;
  const LOG_SCROLL_THRESHOLD = 32;

  function logsAtBottom(box) {
    if (!box) return true;
    return box.scrollHeight - box.scrollTop - box.clientHeight <= LOG_SCROLL_THRESHOLD;
  }

  function bindLogsAutoScroll() {
    const box = document.getElementById('serverLogsBody');
    if (!box || logsScrollBound) return;
    logsScrollBound = true;
    box.addEventListener('scroll', () => {
      logsFollowTail = logsAtBottom(box);
    }, { passive: true });
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function activeServer() {
    return servers.find((s) => s.id === activeId) || allServers.find((s) => s.id === activeId) || servers[0] || null;
  }

  function serverIsLive(server) {
    return !!server && (server.running || server.status === 'booting' || server.status === 'loaded');
  }

  /** Follow backend when another profile was started via API while the UI had a stopped selection. */
  function syncActiveIdFromLiveState() {
    if (busy) return;
    if (serverIsLive(activeServer())) return;
    const live = servers.find((s) => serverIsLive(s));
    if (live && live.id !== activeId) {
      activeId = live.id;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
    }
  }

  function pollIntervalMs() {
    if (busy || servers.some((s) => s.status === 'booting')) return 1000;
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

  function loadedServerCount() {
    return servers.filter((s) => s.status === 'loaded').length;
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
    if (!entries.length && busyAction === 'loading' && loadingModelMeta) {
      const server = servers.find((s) => s.id === loadingModelMeta.server_id) || activeServer();
      if (server) {
        entries.push({
          server,
          row: {
            card_state: 'loading',
            title: loadingModelMeta.label,
            role: 'alias',
            ejectable: true,
            progress: null,
          },
        });
      }
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
    if (busyAction === 'stopping') return 'Stopping…';
    if (busyAction === 'ejecting') return 'Unloading…';
    if (busyAction === 'starting') return 'Starting engine…';
    if (busyAction === 'loading') return 'Loading model…';
    if (booting && loaded) return `${loaded} loaded · ${booting} loading`;
    if (booting) return booting === 1 ? 'Loading model…' : `${booting} models loading`;
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
    return base || row.id || 'checkpoint';
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

  function cardLiveStats(server) {
    const stats = server?.inference_stats;
    if (!stats || server.status !== 'loaded') return '';
    const parts = [];
    if (stats.tokens_loaded != null) parts.push(`${stats.tokens_loaded} ctx`);
    if (stats.generation_tokens != null) parts.push(`${stats.generation_tokens} out`);
    if (stats.tokens_per_second != null) parts.push(`${stats.tokens_per_second} t/s`);
    if (!parts.length) return '';
    return `<span class="lm-model-card-live">${escapeHtml(parts.join(' · '))}</span>`;
  }

  function emptyMessage(server) {
    if (busyAction === 'stopping') return 'Stopping server…';
    if (busyAction === 'ejecting') return 'Unloading model…';
    if (busyAction === 'starting') return 'Starting engine…';
    if (busyAction === 'loading') return 'Loading model…';
    if (server?.status === 'running') return 'Engine is listening but no checkpoint is loaded. Click Run checkpoint.';
    return 'Engine stopped. Turn it on or run a checkpoint.';
  }

  function renderCards() {
    const wrap = document.getElementById('serverModelCards');
    const empty = document.getElementById('serverEmptyState');
    if (!wrap || !empty) return;

    if (busyAction === 'stopping' || busyAction === 'ejecting') {
      wrap.innerHTML = '';
      empty.textContent = emptyMessage(activeServer());
      empty.classList.remove('hidden');
      return;
    }

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
          ? '<button class="lm-btn ghost small" data-action="eject" title="Unload checkpoint">Unload</button>'
          : '<button class="lm-btn ghost small" data-action="cancel-load">Cancel</button>';
      }
      const cardClass = `lm-model-card lm-model-card-compact ${ready ? 'ready' : 'loading'}${loading && progressPct == null ? ' lm-progress-indeterminate' : ''}`;
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
        <article class="${cardClass}" data-server-id="${escapeHtml(server.id)}" data-role="${escapeHtml(row.role)}" title="${escapeHtml(hoverTitle)}"${cardStyle}>
          ${loadChrome}
          <div class="lm-model-card-top">
            ${badge}
            <span class="lm-model-path">${escapeHtml(cardDisplayName(row))}</span>
            ${cardLiveStats(server)}
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
    const parts = [model.label || model.filename || model.id || 'Checkpoint'];
    if (model.quant && model.quant !== '—') parts.push(model.quant);
    if (model.size_gb != null) parts.push(`${model.size_gb} GB`);
    if (model.loadable && model.port) parts.push(`port :${model.port}`);
    else if (!model.loadable) parts.push('browse only');
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
    const parts = ['<option value="">Select checkpoint…</option>'];
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
    if (loadBtn) loadBtn.disabled = busy || !selected?.loadable;
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
    if (loadBtn) loadBtn.disabled = busy || !model?.loadable;
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
    if (!model?.loadable) {
      toast('This checkpoint is browse-only — wire it to an engine profile in Settings.', false);
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
    if (toggle) setRunningToggle(running && busyAction !== 'stopping');
    if (urlEl) urlEl.textContent = server.reachable_url || '—';
    if (loadBtn) {
      const picked = selectedCatalogModel();
      loadBtn.disabled = busy || !picked?.loadable;
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
    if (!logLinesRaw.length) {
      box.innerHTML = '<div class="log-line log-empty"><span class="log-datetime">—</span> <span class="log-dim">No log output yet. Start the engine to capture logs.</span></div>';
      updateLogsCount(0);
      if (stickToBottom) box.scrollTop = box.scrollHeight;
      return;
    }
    if (!displayLines.length) {
      box.innerHTML = `<div class="log-line log-empty"><span class="log-dim">No lines match filter “${escapeHtml(filterLabel)}”.</span></div>`;
      updateLogsCount(0);
      if (stickToBottom) box.scrollTop = box.scrollHeight;
      return;
    }
    box.innerHTML = displayLines.map((line) => (
      format ? format(line) : `<div class="log-line">${escapeHtml(line)}</div>`
    )).join('');
    updateLogsCount(displayLines.length);
    if (stickToBottom) box.scrollTop = box.scrollHeight;
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
    document.getElementById('inspectorHeadTitle')?.replaceChildren(document.createTextNode(model.label || model.id || 'Checkpoint'));
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
    if (server) {
      fillInspectorLoadSettings(server);
      if (document.body.dataset.activeView === 'server') {
        inspectorBound = {
          serverId: server.id,
          modelKey: server.id,
          profile: server.profile,
          context_max: PROFILE_CTX_MAX[server.profile] || server.context_max || 262144,
          gpu_layers_max: server.gpu_layers_max || 128,
        };
      }
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
    if (!server || busy) return;
    if (serverIsLive(server) && server.status !== 'stopped') {
      toast('Engine is already running');
      setRunningToggle(true);
      return;
    }
    busy = true;
    busyAction = 'starting';
    window.DFlashStatusFeed?.setTransient(`Starting engine ${server.label || server.id}…`, {
      secondary: `Port :${server.port}`,
      ttlMs: 120000,
    });
    renderAll();
    try {
      await api(`/api/servers/${encodeURIComponent(server.id)}/listen`, { method: 'POST' });
      toast('Engine started');
      window.DFlashStatusFeed?.note('Engine listening', `Port :${server.port} · no checkpoint loaded yet`);
      await refresh();
    } catch (err) {
      toast(err.message, false);
    } finally {
      busy = false;
      busyAction = null;
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

  async function loadSelectedModel(model) {
    if (!model?.server_id) {
      toast('Only configured server profiles can be loaded here. Add a server in Settings.', false);
      return;
    }
    if (busy) return;
    await applyModelSelection(model);
    activeId = model.server_id;
    localStorage.setItem('dflashConsole.activeServerId', activeId);
    const label = model.label || model.id;
    loadingModelMeta = { server_id: model.server_id, label };
    busy = true;
    busyAction = 'loading';
    window.DFlashStatusFeed?.setTransient(`Loading ${label}…`, {
      secondary: 'Reading weights into GPU',
      ttlMs: 120000,
    });
    renderAll();
    try {
      await saveInspectorLoadSettings();
      const result = await api(`/api/servers/${encodeURIComponent(model.server_id)}/load`, { method: 'POST' });
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
      const loaded = await waitUntilModelLoaded(model.server_id);
      if (loaded?.status === 'loaded') {
        toast('Model loaded');
        window.DFlashStatusFeed?.note(`${label} ready`, `Port :${loaded.port || '—'}`);
      }
    } catch (err) {
      toast(err.message, false);
      window.DFlashStatusFeed?.note('Load failed', err.message || label);
    } finally {
      loadingModelMeta = null;
      busy = false;
      busyAction = null;
      renderAll();
    }
  }

  async function ejectServer(serverId) {
    if (!serverId || busy) return;
    busy = true;
    busyAction = 'ejecting';
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
      busy = false;
      busyAction = null;
      await refresh();
    }
  }

  async function ejectActive() {
    const server = activeServer();
    if (!server) return;
    await ejectServer(server.id);
  }

  async function stopServer(serverId) {
    if (!serverId || busy) return;
    busy = true;
    busyAction = 'stopping';
    const label = allServers.find((s) => s.id === serverId)?.label || serverId;
    window.DFlashStatusFeed?.setTransient(`Stopping ${label}…`, { ttlMs: 30000 });
    renderAll();
    try {
      await api(`/api/servers/${encodeURIComponent(serverId)}/stop`, { method: 'POST' });
      toast('Server stopped');
      await refresh();
    } catch (err) {
      toast(err.message, false);
    } finally {
      busy = false;
      busyAction = null;
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
    document.getElementById('serverLogsFilter')?.addEventListener('change', (e) => {
      logFilterId = e.target.value || 'all';
      localStorage.setItem('dflashConsole.logFilter', logFilterId);
      renderLogs(logLinesRaw);
    });
    const filterEl = document.getElementById('serverLogsFilter');
    if (filterEl) filterEl.value = logFilterId;

    document.getElementById('serverSettingsPick')?.addEventListener('change', (e) => {
      activeId = e.target.value;
      localStorage.setItem('dflashConsole.activeServerId', activeId);
      fillSettingsForm(allServers.find((s) => s.id === activeId) || activeServer());
      void refresh();
    });
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
    fillSettingsForm,
    saveGatewaySettings,
    fillInspectorLoadSettings,
    flushInspectorSave,
    getMergedLoadSettings,
    modelKeyFor,
    syncModelPicker,
  };
})();
