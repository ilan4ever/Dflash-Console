/** Live Server tab — polls /api/servers and renders model stack cards */
(function () {
  const { api, toast } = window.StudioApi;

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
  let activeId = localStorage.getItem('dflashStudio.activeServerId') || '';
  let pollTimer = null;
  let busy = false;
  let busyAction = null;
  const PREFS_KEY = 'dflashStudio.modelPrefs';
  let inspectorBound = null;
  let inspectorFilling = false;
  let autoSaveTimer = null;
  let saveInFlight = null;

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
    if (busyAction === 'ejecting') return 'Ejecting…';
    if (busyAction === 'starting') return 'Loading…';
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
    if (row.role === 'alias') return `llm ${row.id}`;
    if (row.role === 'draft-dflash') return `dflash ${row.id}`;
    if (row.role === 'draft-dspark') return `dspark ${row.id}`;
    const base = row.path ? row.path.split(/[/\\]/).pop() : row.label;
    return `llm ${base}`;
  }

  function roleBadge(row) {
    if (row.role === 'draft-dflash') return '<span class="lm-tag green">DFlash draft</span>';
    if (row.role === 'draft-dspark') return '<span class="lm-tag yellow">dspark draft</span>';
    if (row.role === 'alias') return '<span class="lm-tag blue">API</span>';
    if (row.source === 'lmstudio') return '<span class="lm-tag blue">LM Studio</span>';
    return '';
  }

  function renderStackDetails(row) {
    const details = Array.isArray(row.stack_details) ? row.stack_details : [];
    if (!details.length) return '';
    return `
      <div class="lm-model-stack">
        ${details.map((part) => `
          <div class="lm-model-stack-line">
            <span class="lm-tag dim">${escapeHtml(detailBadge(part.source, part.role))}</span>
            <span>${escapeHtml(part.name)}</span>
            ${part.size_gb != null ? `<span class="lm-stack-size">${part.size_gb} GB</span>` : ''}
          </div>`).join('')}
      </div>`;
  }

  function emptyMessage(server) {
    if (busyAction === 'stopping') return 'Stopping server…';
    if (busyAction === 'ejecting') return 'Ejecting model…';
    if (busyAction === 'starting') return 'Starting server…';
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
      const indeterminate = loading && progressPct == null;
      const badge = ready
        ? '<span class="lm-badge ready">READY</span>'
        : `<span class="lm-badge loading">⟳ LOADING${progressPct != null ? ` ${Math.round(progressPct)}%` : ''}</span>`;
      const subtitleParts = [];
      if (row.subtitle) subtitleParts.push(row.subtitle);
      subtitleParts.push(`:${server.port} · ${server.reachable_url || ''}`);
      const subtitle = `<div class="lm-model-subtitle">${escapeHtml(subtitleParts.join(' · '))}</div>`;
      let action = '';
      if (row.ejectable) {
        action = ready
          ? '<button class="lm-btn ghost" data-action="eject">⏏ Eject</button>'
          : '<button class="lm-btn ghost" data-action="cancel-load">Cancel</button>';
      }
      const cardStyle = progressPct != null ? ` style="--card-progress:${progressPct}%"` : '';
      const cardClass = `lm-model-card ${ready ? 'ready' : 'loading'}${indeterminate ? ' lm-progress-indeterminate' : ''}`;
      const missing = row.path_missing ? '<span class="lm-tag yellow">missing file</span>' : '';
      const serverHead = `<div class="lm-model-card-server">${escapeHtml(server.label || server.id)} <span class="lm-port">:${server.port}</span> · ${escapeHtml(serverStatusLabel(server))}</div>`;

      return `
        <article class="${cardClass}" data-server-id="${escapeHtml(server.id)}" data-role="${escapeHtml(row.role)}"${cardStyle}>
          ${serverHead}
          <div class="lm-model-card-top">
            ${badge}
            <div class="lm-model-title-block">
              <span class="lm-model-path">${escapeHtml(cardDisplayName(row))}</span>
              ${subtitle}
            </div>
            ${roleBadge(row)} ${missing}
            <div class="lm-model-stats">
              ${action}
            </div>
          </div>
          ${renderStackDetails(row)}
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

  function renderServerPicker() {
    const pick = document.getElementById('serverProfilePick');
    if (!pick) return;
    const options = allServers.filter((s) => s.enabled !== false);
    pick.innerHTML = options.map((s) => {
      const mark = s.status === 'loaded' ? '●' : s.status === 'booting' ? '◐' : s.running ? '○' : '·';
      return `<option value="${escapeHtml(s.id)}"${s.id === activeId ? ' selected' : ''}>${mark} ${escapeHtml(s.label || s.id)} :${s.port}</option>`;
    }).join('');
  }

  function renderToolbar(server) {
    const statusText = document.getElementById('serverStatusText');
    const toggle = document.getElementById('serverRunningToggle');
    const urlEl = document.getElementById('serverReachableUrl');
    const loadBtn = document.getElementById('serverLoadBtn');

    renderServerPicker();

    if (!server) {
      if (statusText) { statusText.textContent = 'No server'; statusText.className = 'lm-status-stopped'; }
      if (toggle) toggle.checked = false;
      if (urlEl) urlEl.textContent = '—';
      if (loadBtn) loadBtn.disabled = true;
      return;
    }

    const running = server.running || server.status === 'booting';
    const label = aggregateStatusLabel();

    if (statusText) {
      statusText.textContent = label;
      const anyActive = loadedServerCount() > 0 || bootingServerCount() > 0 || server.running || server.status === 'booting';
      statusText.className = anyActive ? 'lm-status-running' : 'lm-status-stopped';
    }
    if (toggle) toggle.checked = running && busyAction !== 'stopping';
    if (urlEl) urlEl.textContent = server.reachable_url || '—';
    if (loadBtn) loadBtn.disabled = busy;
  }

  function renderLogs(lines) {
    const box = document.getElementById('serverLogsBody');
    if (!box) return;
    if (!lines.length) {
      box.innerHTML = '<div class="log-line"><span class="ts">—</span> <span class="dbg">No log output yet. Start the server from here to capture logs.</span></div>';
      return;
    }
    box.innerHTML = lines.slice(-200).map((line) => {
      const cls = line.includes('[WARN]') ? 'warn' : line.includes('[INFO]') ? 'info' : 'dbg';
      return `<div class="log-line"><span class="ts"></span> <span class="${cls}">${escapeHtml(line)}</span></div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
  }

  function bindRangePair(numEl, rangeEl) {
    if (!numEl || !rangeEl) return;
    rangeEl.addEventListener('input', () => { numEl.value = rangeEl.value; });
    numEl.addEventListener('input', () => {
      const min = Number(numEl.min || rangeEl.min || 0);
      const max = Number(numEl.max || rangeEl.max || 999999);
      let value = Number(numEl.value || rangeEl.value || min);
      value = Math.min(max, Math.max(min, value));
      numEl.value = String(value);
      rangeEl.value = String(value);
    });
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
      },
    };
  }

  function fillInspectorLoadSettings(server) {
    if (!server) return;
    inspectorFilling = true;
    try {
    const load = server.load_settings || {};
    const ctxMax = PROFILE_CTX_MAX[server.profile] || server.context_max || 262144;
    const gpuMax = server.gpu_layers_max || 128;
    const ctxEl = document.getElementById('inspectorContext');
    const ctxRange = document.getElementById('inspectorContextRange');
    const ctxHint = document.getElementById('inspectorContextHint');
    if (ctxEl) ctxEl.value = server.context_size || 65536;
    if (ctxRange) {
      ctxRange.max = String(ctxMax);
      ctxRange.value = String(server.context_size || 65536);
    }
    if (ctxEl) ctxEl.max = String(ctxMax);
    if (ctxHint) ctxHint.textContent = `Model supports up to ${ctxMax} tokens.`;

    const gpuEl = document.getElementById('inspectorGpuLayers');
    const gpuRange = document.getElementById('inspectorGpuLayersRange');
    const gpuHint = document.getElementById('inspectorGpuHint');
    const gpuLayers = load.gpu_layers ?? 99;
    if (gpuEl) {
      gpuEl.max = String(gpuMax);
      gpuEl.value = gpuLayers;
    }
    if (gpuRange) {
      gpuRange.max = String(gpuMax);
      gpuRange.value = gpuLayers;
    }
    if (gpuHint) gpuHint.textContent = `Layers on GPU (-ngl). Max ${gpuMax}; 99 = all layers.`;

    document.getElementById('inspectorCpuThreads').value = load.cpu_threads ?? 9;
    document.getElementById('inspectorCpuThreadsRange').value = load.cpu_threads ?? 9;
    document.getElementById('inspectorEvalBatch').value = load.eval_batch_size ?? 2048;
    document.getElementById('inspectorPhysicalBatch').value = load.physical_batch_size ?? 512;
    document.getElementById('inspectorFlashAttention').checked = load.flash_attention !== false;

    const infer = server.inference_settings || {};
    const temperature = infer.temperature ?? 0.7;
    const topP = infer.top_p ?? 0.9;
    const topK = infer.top_k ?? 40;
    const repeatPenalty = infer.repeat_penalty ?? 1.1;
    const tempEl = document.getElementById('inspectorTemperature');
    const tempRange = document.getElementById('inspectorTemperatureRange');
    if (tempEl) tempEl.value = temperature;
    if (tempRange) tempRange.value = temperature;
    const topPEl = document.getElementById('inspectorTopP');
    const topPRange = document.getElementById('inspectorTopPRange');
    if (topPEl) topPEl.value = topP;
    if (topPRange) topPRange.value = topP;
    const topKEl = document.getElementById('inspectorTopK');
    const topKRange = document.getElementById('inspectorTopKRange');
    if (topKEl) topKEl.value = topK;
    if (topKRange) topKRange.value = topK;
    const repeatEl = document.getElementById('inspectorRepeatPenalty');
    const repeatRange = document.getElementById('inspectorRepeatPenaltyRange');
    if (repeatEl) repeatEl.value = repeatPenalty;
    if (repeatRange) repeatRange.value = repeatPenalty;

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
    const loadBtn = document.getElementById('inspectorLoadBtn');
    if (loadBtn) loadBtn.disabled = !model.server_id;
  }

  async function applyModelSelection(model) {
    if (!model) return;
    await flushInspectorSave();
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
      localStorage.setItem('dflashStudio.activeServerId', activeId);
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
    const data = await api(`/api/logs/${encodeURIComponent(server.id)}?tail=200`);
    renderLogs(data.lines || []);
  }

  async function refresh() {
    const data = await api('/api/servers');
    servers = data.servers || [];
    allServers = data.all_servers || servers;
    gpus = data.gpus || [];
    if (!activeId || !allServers.some((s) => s.id === activeId)) {
      activeId = data.primary_server_id || servers[0]?.id || allServers[0]?.id || '';
      localStorage.setItem('dflashStudio.activeServerId', activeId);
    }
    renderAll();
    await refreshLogs();
  }

  async function startActive() {
    const server = activeServer();
    if (!server || busy) return;
    busy = true;
    busyAction = 'starting';
    window.DFlashStatusFeed?.setTransient(`Starting ${server.label || server.id}…`, {
      secondary: `Port :${server.port}`,
      ttlMs: 120000,
    });
    renderAll();
    try {
      await saveInspectorLoadSettings();
      const result = await api(`/api/servers/${encodeURIComponent(server.id)}/start`, { method: 'POST' });
      if (result?.memory_warning) {
        toast(result.memory_warning);
        window.DFlashStatusFeed?.note(result.memory_warning, server.label || server.id);
      }
      toast('Starting server…');
      window.DFlashStatusFeed?.setTransient(`Loading ${server.label || server.id}…`, {
        secondary: 'Reading weights into GPU',
        ttlMs: 120000,
      });
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
    await applyModelSelection(model);
    await startActive();
  }

  async function ejectServer(serverId) {
    if (!serverId || busy) return;
    busy = true;
    busyAction = 'ejecting';
    const label = allServers.find((s) => s.id === serverId)?.label || serverId;
    window.DFlashStatusFeed?.setTransient(`Ejecting ${label}…`, { ttlMs: 30000 });
    renderAll();
    try {
      await api(`/api/servers/${encodeURIComponent(serverId)}/unload`, { method: 'POST' });
      toast('Model ejected — server still running');
      await waitUntilServerIdle(serverId);
      activeId = serverId;
      localStorage.setItem('dflashStudio.activeServerId', activeId);
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
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      const view = document.body.dataset.activeView;
      if (view === 'server') void refresh();
      else if (view === 'models' && window.DFlashModelsLive) void window.DFlashModelsLive.refresh().catch(() => {});
    }, 3000);
  }

  function bind() {
    bindRangePair(document.getElementById('inspectorContext'), document.getElementById('inspectorContextRange'));
    bindRangePair(document.getElementById('inspectorGpuLayers'), document.getElementById('inspectorGpuLayersRange'));
    bindRangePair(document.getElementById('inspectorCpuThreads'), document.getElementById('inspectorCpuThreadsRange'));
    bindRangePair(document.getElementById('inspectorTemperature'), document.getElementById('inspectorTemperatureRange'));
    bindRangePair(document.getElementById('inspectorTopP'), document.getElementById('inspectorTopPRange'));
    bindRangePair(document.getElementById('inspectorTopK'), document.getElementById('inspectorTopKRange'));
    bindRangePair(document.getElementById('inspectorRepeatPenalty'), document.getElementById('inspectorRepeatPenaltyRange'));

    const autoSaveIds = [
      'inspectorContext', 'inspectorContextRange', 'inspectorGpuLayers', 'inspectorGpuLayersRange',
      'inspectorCpuThreads', 'inspectorCpuThreadsRange', 'inspectorEvalBatch', 'inspectorPhysicalBatch',
      'inspectorFlashAttention', 'inspectorTemperature', 'inspectorTemperatureRange', 'inspectorTopP',
      'inspectorTopPRange', 'inspectorTopK', 'inspectorTopKRange', 'inspectorRepeatPenalty',
      'inspectorRepeatPenaltyRange',
    ];
    autoSaveIds.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      const eventName = el.type === 'checkbox' ? 'change' : 'input';
      el.addEventListener(eventName, scheduleInspectorAutoSave);
    });

    document.getElementById('serverRunningToggle')?.addEventListener('change', (e) => {
      if (e.target.checked) void startActive();
      else void stopActive();
    });
    document.getElementById('serverLoadBtn')?.addEventListener('click', () => void startActive());
    document.getElementById('inspectorLoadBtn')?.addEventListener('click', () => void startActive());
    document.getElementById('serverCopyUrl')?.addEventListener('click', () => {
      const url = document.getElementById('serverReachableUrl')?.textContent;
      if (url && url !== '—') navigator.clipboard.writeText(url).then(() => toast('URL copied'));
    });
    document.getElementById('serverLogsRefresh')?.addEventListener('click', () => void refreshLogs().catch((e) => toast(e.message, false)));

    document.getElementById('serverSettingsPick')?.addEventListener('change', (e) => {
      activeId = e.target.value;
      localStorage.setItem('dflashStudio.activeServerId', activeId);
      fillSettingsForm(allServers.find((s) => s.id === activeId) || activeServer());
      void refresh();
    });

    document.getElementById('serverProfilePick')?.addEventListener('change', (e) => {
      activeId = e.target.value;
      localStorage.setItem('dflashStudio.activeServerId', activeId);
      renderAll();
      void refreshLogs().catch((err) => toast(err.message, false));
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
  };
})();
