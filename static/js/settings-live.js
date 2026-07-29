/** Settings modal — nested nav, workspace / compute / engines */
(function () {
  const { api, toast } = window.ConsoleApi;

  let hardwareData = null;
  let hardwareDraft = null;
  let librariesDraft = null;
  let librariesSaveTimer = null;
  let saveTimer = null;
  let pollTimer = null;
  let filling = false;
  const DEFAULT_PANEL = 'ws-checkpoints';
  const HW_PANELS = new Set(['hw-system', 'hw-gpus', 'hw-strategy', 'hw-live']);
  const GW_PANELS = new Set(['gw-network', 'gw-behavior', 'gw-preset']);

  const STRATEGY_HINTS = {
    single_largest: 'Use one GPU — the enabled card with the most VRAM.',
    split_evenly: 'Spread model layers evenly across all enabled GPUs.',
    split_by_vram: 'Split large models across GPUs in proportion to VRAM.',
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function settingsModal() {
    return document.getElementById('settingsModal');
  }

  function activePanelId() {
    const panel = document.querySelector('.lm-settings-panel.active');
    return panel?.dataset.settingsPanel || DEFAULT_PANEL;
  }

  function openSettingsModal() {
    const modal = settingsModal();
    if (!modal) return;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    if (window.syncSysbarHeightVar) window.syncSysbarHeightVar();
  }

  function showPanel(id) {
    const panelId = id || DEFAULT_PANEL;
    document.querySelectorAll('.lm-settings-nav-item[data-settings-panel]').forEach((item) => {
      item.classList.toggle('active', item.dataset.settingsPanel === panelId);
    });
    document.querySelectorAll('.lm-settings-panel').forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.settingsPanel === panelId);
    });
    if (HW_PANELS.has(panelId)) renderHardwareForPanel(panelId);
    if (GW_PANELS.has(panelId)) renderGatewayPanel();
    if (panelId === 'int-mcp') void renderMcpPanel();
  }

  function enabledIndicesFromDraft() {
    const list = hardwareDraft?.enabled_gpu_indices;
    if (Array.isArray(list) && list.length) return list.map((n) => Number(n));
    return (hardwareData?.gpus || []).map((g) => Number(g.index));
  }

  function isGpuEnabled(index) {
    const enabled = hardwareDraft?.enabled_gpu_indices;
    if (!Array.isArray(enabled) || !enabled.length) return true;
    return enabled.includes(Number(index));
  }

  function renderWorkspacePaths() {
    if (!hardwareData) return;
    const dflashRoot = document.getElementById('settingsDflashRoot');
    const logsDir = document.getElementById('settingsLogsDir');
    const presetsDir = document.getElementById('settingsPresetsDir');
    const uiPort = document.getElementById('settingsUiPort');
    if (dflashRoot) dflashRoot.textContent = hardwareData.dflash_root || '—';
    if (logsDir) logsDir.textContent = hardwareData.logs_dir || '—';
    if (presetsDir) presetsDir.textContent = hardwareData.presets_dir || '—';
    if (uiPort) uiPort.textContent = hardwareData.ui_port ? `http://127.0.0.1:${hardwareData.ui_port}/` : '—';
    renderModelLibraries();
  }

  function renderModelLibraries() {
    if (!librariesDraft) librariesDraft = [...(hardwareData?.model_libraries || [])];
    const list = document.getElementById('settingsModelLibraries');
    const downloadPick = document.getElementById('settingsDownloadLibrary');
    if (downloadPick) {
      downloadPick.innerHTML = librariesDraft
        .filter((row) => row.enabled !== false)
        .map((row) =>
          `<option value="${escapeHtml(row.id)}"${row.download_default ? ' selected' : ''}>${escapeHtml(row.label || row.id)}</option>`,
        ).join('');
    }
    if (!list) return;
    if (!librariesDraft.length) {
      list.innerHTML = '<p class="lm-setting-desc">No library folders configured yet.</p>';
      return;
    }
    list.innerHTML = librariesDraft.map((row, index) => {
      const removable = !(row.preset === 'dflash' && librariesDraft.filter((r) => r.preset === 'dflash').length <= 1);
      const count = row.model_count != null ? `${row.model_count} models` : '';
      const typeTag = row.model_type ? `<span class="lm-tag dim">${escapeHtml(String(row.model_type).toUpperCase())}</span>` : '';
      const presetTag = row.preset ? `<span class="lm-tag">${escapeHtml(row.preset)}</span>` : '';
      const samples = (row.sample_models || []).slice(0, 2).map((s) => `<span class="lm-tag dim">${escapeHtml(s)}</span>`).join('');
      return `
        <div class="lm-library-card" data-library-index="${index}">
          <div class="lm-library-card-main">
            <div class="lm-library-card-title">${escapeHtml(row.label || row.id)} ${presetTag} ${typeTag}</div>
            <code class="lm-settings-path">${escapeHtml(row.path || '—')}</code>
            <div class="lm-library-card-meta">${escapeHtml(count)}${samples ? ` · ${samples}` : ''}</div>
          </div>
          <div class="lm-library-card-actions">
            <label class="lm-toggle small" title="${row.enabled !== false ? 'Scan enabled' : 'Scan disabled'}">
              <input type="checkbox" data-library-enabled="${index}"${row.enabled !== false ? ' checked' : ''}>
              <span class="lm-toggle-track"></span>
            </label>
            ${removable ? `<button type="button" class="lm-icon-btn tiny" data-library-remove="${index}" title="Remove">✕</button>` : ''}
          </div>
        </div>`;
    }).join('');

    list.querySelectorAll('[data-library-enabled]').forEach((input) => {
      input.addEventListener('change', () => {
        const index = Number(input.dataset.libraryEnabled);
        if (!librariesDraft[index]) return;
        librariesDraft[index].enabled = input.checked;
        if (!input.checked && librariesDraft[index].download_default) {
          const fallback = librariesDraft.find((row, idx) => idx !== index && row.enabled !== false);
          librariesDraft.forEach((row) => { row.download_default = false; });
          if (fallback) fallback.download_default = true;
        }
        renderModelLibraries();
        scheduleLibrariesSave();
      });
    });
    list.querySelectorAll('[data-library-remove]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const index = Number(btn.dataset.libraryRemove);
        librariesDraft.splice(index, 1);
        if (!librariesDraft.some((row) => row.download_default)) {
          const fallback = librariesDraft.find((row) => row.enabled !== false) || librariesDraft[0];
          if (fallback) fallback.download_default = true;
        }
        renderModelLibraries();
        scheduleLibrariesSave();
      });
    });
  }

  function libraryPathExists(path) {
    const key = String(path || '').trim().toLowerCase();
    const list = librariesDraft || hardwareData?.model_libraries || [];
    return list.some((row) => String(row.path || '').trim().toLowerCase() === key);
  }

  function addLibraryFromBrowse(entry, options = {}) {
    if (!entry?.path) {
      toast('Could not resolve library path', false);
      return;
    }
    if (libraryPathExists(entry.path)) {
      toast('That folder is already in your library list', false);
      return;
    }
    librariesDraft = librariesDraft || [...(hardwareData?.model_libraries || [])];
    const presetId = entry.preset || 'custom';
    librariesDraft.push({
      id: `${presetId}-${Date.now()}`,
      label: entry.label || entry.path,
      preset: presetId,
      path: entry.path,
      enabled: true,
      download_default: librariesDraft.length === 0,
      model_count: entry.model_count,
      model_type: entry.model_type,
      sample_models: entry.sample_models,
    });
    renderModelLibraries();
    scheduleLibrariesSave();
    if (!options.quiet) toast('Folder added to library');
  }

  async function importLibraryAndAdd(entry, mode) {
    const importMode = mode || 'link';
    if (!entry?.path) {
      toast('Could not resolve library path', false);
      return false;
    }
    if (importMode === 'move') {
      const ok = window.confirm(
        'Move will transfer model files into your DFlash library folder and remove them from the original location. Continue?',
      );
      if (!ok) return false;
    }
    try {
      let resolved = { ...entry };
      if (importMode !== 'link') {
        const data = await api('/api/model-libraries/import', {
          method: 'POST',
          body: JSON.stringify({
            path: entry.path,
            preset: entry.preset || 'dflash',
            mode: importMode,
          }),
        });
        resolved = { ...entry, ...(data.library || {}), path: data.library_path || data.library?.path || entry.path };
        const verb = importMode === 'move' ? 'Moved' : 'Copied';
        toast(`${verb} models into DFlash library`);
      }
      if (libraryPathExists(resolved.path)) {
        toast('That folder is already in your library list', false);
        return true;
      }
      addLibraryFromBrowse(resolved, { quiet: importMode !== 'link' });
      return true;
    } catch (err) {
      toast(err.message, false);
      return false;
    }
  }

  async function importLibrariesAndAdd(rows, mode) {
    if (!Array.isArray(rows) || !rows.length) return;
    const importMode = mode || 'link';
    if (importMode === 'move') {
      const ok = window.confirm(
        `Move will transfer models from ${rows.length} folder${rows.length === 1 ? '' : 's'} into your DFlash library and remove them from the original locations. Continue?`,
      );
      if (!ok) return;
    }
    let added = 0;
    for (const row of rows) {
      if (!row?.path) continue;
      const done = await importLibraryAndAdd(row, importMode);
      if (done) added += 1;
    }
    if (!added) {
      toast('No folders were added', false);
    }
  }

  function addLibrariesFromScan(rows) {
    if (!Array.isArray(rows) || !rows.length) return;
    librariesDraft = librariesDraft || [...(hardwareData?.model_libraries || [])];
    let added = 0;
    for (const row of rows) {
      if (!row?.path || libraryPathExists(row.path)) continue;
      librariesDraft.push({
        id: `${row.preset || 'custom'}-${Date.now()}-${added}`,
        label: row.label,
        preset: row.preset || 'custom',
        path: row.path,
        enabled: true,
        download_default: false,
        model_count: row.model_count,
        model_type: row.model_type,
        sample_models: row.sample_models,
      });
      added += 1;
    }
    if (!added) {
      toast('Selected folders are already in your library list', false);
      return;
    }
    renderModelLibraries();
    scheduleLibrariesSave();
    toast(`Added ${added} library location${added === 1 ? '' : 's'}`);
  }

  function openLibraryScan() {
    window.DFlashLibraryScan?.openScan?.('dflash');
  }

  function openLibraryBrowse() {
    window.DFlashLibraryBrowse?.openBrowse?.('dflash');
  }

  async function persistLibraries() {
    const downloadId = document.getElementById('settingsDownloadLibrary')?.value || '';
    librariesDraft = (librariesDraft || []).map((row) => ({
      ...row,
      download_default: row.id === downloadId,
    }));
    const result = await api('/api/config', {
      method: 'PUT',
      body: JSON.stringify({ model_libraries: librariesDraft }),
    });
    librariesDraft = [...(result.config?.model_libraries || librariesDraft)];
    hardwareData.model_libraries = [...librariesDraft];
    hardwareData.download_library_id = downloadId;
    const primary = librariesDraft.find((row) => row.id === downloadId);
    if (primary?.path) hardwareData.models_dir = primary.path;
    toast('Library locations saved');
    if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
  }

  function scheduleLibrariesSave() {
    if (librariesSaveTimer) clearTimeout(librariesSaveTimer);
    librariesSaveTimer = window.setTimeout(() => {
      librariesSaveTimer = null;
      persistLibraries().catch((err) => toast(err.message, false));
    }, 450);
  }

  function renderHardwareSummary() {
    const el = document.getElementById('settingsHwSummary');
    if (!el || !hardwareData) return;
    const cpu = hardwareData.cpu || {};
    const ram = hardwareData.ram || {};
    const features = (cpu.features || []).map((f) => `<span class="lm-tag">${escapeHtml(f)}</span>`).join('');
    el.innerHTML = `
      <div class="lm-hw-card">
        <strong>${escapeHtml(cpu.name || 'CPU')}</strong>
        <span class="lm-tag">${escapeHtml(cpu.arch || '—')}</span>${features}
        <span class="lm-tag green">Compatible</span>
      </div>
      <div class="lm-hw-stats-row">
        <div class="lm-hw-stat"><span>RAM</span><strong>${escapeHtml(ram.total_gb != null ? `${ram.total_gb} GB` : '—')}</strong></div>
        <div class="lm-hw-stat"><span>VRAM (total)</span><strong>${escapeHtml(hardwareData.vram_total_gb != null ? `${hardwareData.vram_total_gb} GB` : '—')}</strong></div>
      </div>`;
  }

  function renderGpuList() {
    const list = document.getElementById('settingsGpuList');
    const detect = document.getElementById('settingsGpuDetectLine');
    if (!list || !hardwareData) return;
    const gpus = hardwareData.gpus || [];
    if (detect) {
      detect.textContent = gpus.length
        ? `${gpus.length} GPU${gpus.length === 1 ? '' : 's'} detected with CUDA`
        : 'No NVIDIA GPUs detected.';
    }
    if (!gpus.length) {
      list.innerHTML = '<p class="lm-setting-desc">Connect an NVIDIA GPU and ensure nvidia-smi is available.</p>';
      return;
    }
    list.innerHTML = gpus.map((gpu) => {
      const on = isGpuEnabled(gpu.index);
      const vram = gpu.vram_total_gb ?? gpu.vram_gb;
      return `
        <div class="lm-hw-card lm-hw-gpu-card">
          <div>
            <strong>${escapeHtml(gpu.name || gpu.display_name || `GPU ${gpu.index}`)}</strong>
            <p class="lm-setting-desc">${escapeHtml(vram != null ? `${vram} GB VRAM` : 'VRAM —')} · CUDA · device ${gpu.index}</p>
          </div>
          <label class="lm-toggle small" title="${on ? 'GPU enabled' : 'GPU disabled'}">
            <input type="checkbox" data-gpu-toggle="${gpu.index}"${on ? ' checked' : ''}>
            <span class="lm-toggle-track"></span>
          </label>
        </div>`;
    }).join('');

    list.querySelectorAll('[data-gpu-toggle]').forEach((input) => {
      input.addEventListener('change', () => {
        const index = Number(input.dataset.gpuToggle);
        let enabled = enabledIndicesFromDraft();
        if (input.checked) {
          if (!enabled.includes(index)) enabled.push(index);
        } else {
          enabled = enabled.filter((n) => n !== index);
          if (!enabled.length) {
            input.checked = true;
            toast('At least one GPU must stay enabled', false);
            return;
          }
        }
        hardwareDraft.enabled_gpu_indices = enabled.sort((a, b) => a - b);
        scheduleHardwareSave();
      });
    });
  }

  function renderMonitor() {
    const el = document.getElementById('settingsHwMonitor');
    if (!el || !hardwareData) return;
    const ram = hardwareData.ram || {};
    const cpuPct = hardwareData.cpu_percent;
    const gpus = hardwareData.gpus || [];
    const gpuLines = gpus.map((gpu) => {
      const used = gpu.vram_used_gb != null ? `${gpu.vram_used_gb}` : '0';
      const total = gpu.vram_total_gb ?? gpu.vram_gb ?? '—';
      return `<div class="lm-hw-monitor-row"><span>${escapeHtml(gpu.display_name || gpu.name || `GPU ${gpu.index}`)}</span><strong>${escapeHtml(used)} / ${escapeHtml(total)} GB · ${gpu.load_percent ?? 0}%</strong></div>`;
    }).join('');
    el.innerHTML = `
      <div class="lm-hw-monitor-row"><span>RAM</span><strong>${escapeHtml(ram.used_gb ?? 0)} / ${escapeHtml(ram.total_gb ?? '—')} GB · ${ram.percent ?? 0}%</strong></div>
      <div class="lm-hw-monitor-row"><span>CPU</span><strong>${cpuPct != null ? `${cpuPct}%` : '—'}</strong></div>
      ${gpuLines || '<div class="lm-hw-monitor-row"><span>GPU</span><strong>—</strong></div>'}`;
  }

  function fillHardwareForm() {
    if (!hardwareDraft) return;
    filling = true;
    const strategy = document.getElementById('settingsGpuStrategy');
    const hint = document.getElementById('settingsGpuStrategyHint');
    const dedicated = document.getElementById('settingsLimitDedicatedVram');
    const kv = document.getElementById('settingsOffloadKvGpu');
    if (strategy) strategy.value = hardwareDraft.gpu_strategy || 'split_evenly';
    if (hint) hint.textContent = STRATEGY_HINTS[strategy?.value || 'split_evenly'] || STRATEGY_HINTS.split_evenly;
    if (dedicated) dedicated.checked = hardwareDraft.limit_offload_dedicated_vram !== false;
    if (kv) kv.checked = hardwareDraft.offload_kv_cache_to_gpu !== false;
    renderGpuList();
    filling = false;
  }

  function readHardwareDraftFromForm() {
    if (!hardwareDraft) hardwareDraft = {};
    const strategy = document.getElementById('settingsGpuStrategy');
    hardwareDraft.gpu_strategy = strategy?.value || 'split_evenly';
    hardwareDraft.limit_offload_dedicated_vram = !!document.getElementById('settingsLimitDedicatedVram')?.checked;
    hardwareDraft.offload_kv_cache_to_gpu = !!document.getElementById('settingsOffloadKvGpu')?.checked;
    if (!Array.isArray(hardwareDraft.enabled_gpu_indices) || !hardwareDraft.enabled_gpu_indices.length) {
      hardwareDraft.enabled_gpu_indices = (hardwareData?.gpus || []).map((g) => Number(g.index));
    }
  }

  function renderHardwareForPanel(panelId) {
    if (panelId === 'hw-system') renderHardwareSummary();
    if (panelId === 'hw-gpus') {
      fillHardwareForm();
      renderGpuList();
    }
    if (panelId === 'hw-strategy') fillHardwareForm();
    if (panelId === 'hw-live') renderMonitor();
  }

  function renderGatewayPanel() {
    const live = window.DFlashServerLive;
    if (!live?.fillSettingsForm) return;
    void live.refresh?.().then(() => {
      live.fillSettingsForm(live.activeServer?.());
      updateGatewaySummary();
      updateGatewayApiUrl();
      void window.DFlashDocsLive?.renderSettingsList?.();
    }).catch(() => {
      live.fillSettingsForm(live.activeServer?.());
      updateGatewaySummary();
      updateGatewayApiUrl();
      void window.DFlashDocsLive?.renderSettingsList?.();
    });
  }

  function buildMcpJson(servers) {
    const engines = (servers || []).map((server) => ({
      id: server.id,
      label: server.label || server.id,
      openai_base_url: server.api_url || server.reachable_url || `http://${server.host || '127.0.0.1'}:${server.port || 0}/v1`,
      enabled: server.enabled !== false,
    }));
    return {
      mcpServers: {},
      dflashConsole: {
        note: 'MCP host is not active in DFlash Console yet. Use openai_base_url values with OpenAI-compatible clients.',
        engines,
      },
    };
  }

  async function renderMcpPanel() {
    const listEl = document.getElementById('settingsMcpEngines');
    const jsonEl = document.getElementById('settingsMcpJson');
    if (!listEl || !jsonEl) return;
    listEl.innerHTML = '<p class="lm-setting-desc">Loading engine list…</p>';
    try {
      const data = await api('/api/servers');
      const servers = (data.all_servers || data.servers || []).filter((row) => row.enabled !== false);
      if (!servers.length) {
        listEl.innerHTML = '<p class="lm-setting-desc">No engine profiles configured.</p>';
      } else {
        listEl.innerHTML = servers.map((server) => {
          const url = server.api_url || server.reachable_url || `http://${server.host || '127.0.0.1'}:${server.port || 0}/v1`;
          const status = server.status === 'loaded' ? 'Loaded' : server.running ? 'Running' : 'Stopped';
          const statusClass = server.status === 'loaded' || server.running ? 'green' : 'dim';
          return `
            <div class="lm-mcp-engine-card">
              <div><strong>${escapeHtml(server.label || server.id)}</strong> <span class="lm-tag ${statusClass}">${escapeHtml(status)}</span></div>
              <code>${escapeHtml(url)}</code>
            </div>`;
        }).join('');
      }
      jsonEl.textContent = JSON.stringify(buildMcpJson(servers), null, 2);
    } catch (err) {
      listEl.innerHTML = `<p class="lm-setting-desc">${escapeHtml(err.message)}</p>`;
      jsonEl.textContent = '{}';
    }
  }

  function copyMcpJson() {
    const text = document.getElementById('settingsMcpJson')?.textContent;
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => toast('mcp.json copied'));
  }

  function updateGatewayApiUrl() {
    const host = document.getElementById('serverSettingsHost')?.value?.trim() || '127.0.0.1';
    const port = document.getElementById('serverSettingsPort')?.value || '';
    const urlEl = document.getElementById('settingsGatewayApiUrl');
    if (urlEl && port) urlEl.textContent = `http://${host}:${port}/v1`;
  }

  function updateGatewaySummary() {
    const el = document.getElementById('settingsGatewaySummary');
    const server = window.DFlashServerLive?.activeServer?.();
    if (!el || !server) {
      if (el) el.textContent = 'No engine profile selected.';
      return;
    }
    el.innerHTML = `
      <div><strong>${escapeHtml(server.label || server.id)}</strong></div>
      <div>Profile ID · ${escapeHtml(server.id)}</div>
      <div>Preset · ${escapeHtml(server.profile || '—')}</div>
      <div>Port · ${escapeHtml(server.port)} · Context · ${escapeHtml(server.context_size)}</div>`;
  }

  async function fetchHardware({ silent = false } = {}) {
    const stats = await api('/api/system-stats').catch(() => ({}));
    const data = await api('/api/hardware');
    hardwareData = {
      ...data,
      cpu_percent: stats.cpu_percent,
      ram: {
        ...(data.ram || {}),
        used_gb: stats.ram_used_gb ?? data.ram?.used_gb,
        total_gb: stats.ram_total_gb ?? data.ram?.total_gb,
        percent: stats.ram_percent ?? data.ram?.percent,
      },
      gpus: (data.gpus || []).map((gpu) => {
        const live = (stats.gpus || []).find((g) => Number(g.index) === Number(gpu.index)) || {};
        return { ...gpu, ...live, index: gpu.index };
      }),
    };
    if (!hardwareDraft) {
      hardwareDraft = { ...(data.hardware_settings || {}) };
      delete hardwareDraft.guardrails;
      delete hardwareDraft.guardrails_max_model_gb;
    }
    librariesDraft = [...(data.model_libraries || hardwareData?.model_libraries || [])];
    renderWorkspacePaths();
    const panel = activePanelId();
    if (HW_PANELS.has(panel)) renderHardwareForPanel(panel);
    if (GW_PANELS.has(panel)) renderGatewayPanel();
    if (!silent) return data;
    return data;
  }

  async function persistHardware() {
    readHardwareDraftFromForm();
    const result = await api('/api/hardware', {
      method: 'PATCH',
      body: JSON.stringify(hardwareDraft),
    });
    hardwareDraft = { ...(result.hardware_settings || hardwareDraft) };
    toast('Compute settings saved');
  }

  function scheduleHardwareSave() {
    if (filling) return;
    readHardwareDraftFromForm();
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      saveTimer = null;
      persistHardware().catch((err) => toast(err.message, false));
    }, 450);
  }

  function scheduleGatewaySave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      saveTimer = null;
      updateGatewayApiUrl();
      window.DFlashServerLive?.saveGatewaySettings?.()
        .then(() => {
          updateGatewaySummary();
          toast('Engine settings saved');
        })
        .catch((err) => toast(err.message, false));
    }, 450);
  }

  function copyHardwareInfo() {
    if (!hardwareData) return;
    const lines = [
      `CPU: ${hardwareData.cpu?.name || '—'}`,
      `RAM: ${hardwareData.ram?.total_gb || '—'} GB`,
      `VRAM total: ${hardwareData.vram_total_gb || '—'} GB`,
      ...(hardwareData.gpus || []).map((g) => `GPU ${g.index}: ${g.name} (${g.vram_total_gb ?? g.vram_gb ?? '?'} GB)`),
      `Strategy: ${hardwareDraft?.gpu_strategy || 'split_evenly'}`,
    ];
    navigator.clipboard.writeText(lines.join('\n')).then(() => toast('Hardware info copied'));
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      const modal = settingsModal();
      if (!modal?.classList.contains('open')) return;
      void fetchHardware({ silent: true }).then(() => {
        const panel = activePanelId();
        if (panel === 'hw-system') renderHardwareSummary();
        if (panel === 'hw-live') renderMonitor();
      }).catch(() => {});
    }, 4000);
  }

  function stopPolling() {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
  }

  function openSettings(panelId = DEFAULT_PANEL) {
    openSettingsModal();
    showPanel(panelId);
    void fetchHardware().then(startPolling).catch((err) => toast(err.message, false));
  }

  function bind() {
    document.querySelector('[data-action="open-settings"]')?.addEventListener('click', () => {
      openSettings(DEFAULT_PANEL);
    });

    document.querySelectorAll('[data-action="open-settings-panel"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        openSettings(btn.dataset.settingsPanel || DEFAULT_PANEL);
      });
    });

    document.querySelector('[data-action="open-hardware-settings"]')?.addEventListener('click', () => {
      openSettings('hw-strategy');
    });

    document.querySelectorAll('.lm-settings-nav-item[data-settings-panel]').forEach((item) => {
      item.addEventListener('click', () => {
        showPanel(item.dataset.settingsPanel);
      });
    });

    document.getElementById('settingsGpuStrategy')?.addEventListener('change', (e) => {
      const hint = document.getElementById('settingsGpuStrategyHint');
      if (hint) hint.textContent = STRATEGY_HINTS[e.target.value] || STRATEGY_HINTS.split_evenly;
      scheduleHardwareSave();
    });
    document.getElementById('settingsLimitDedicatedVram')?.addEventListener('change', scheduleHardwareSave);
    document.getElementById('settingsOffloadKvGpu')?.addEventListener('change', scheduleHardwareSave);
    document.getElementById('settingsHardwareCopy')?.addEventListener('click', copyHardwareInfo);
    document.getElementById('settingsMcpCopy')?.addEventListener('click', copyMcpJson);
    document.getElementById('settingsAddLibrary')?.addEventListener('click', openLibraryBrowse);
    document.getElementById('settingsScanLibrary')?.addEventListener('click', openLibraryScan);
    document.getElementById('settingsDownloadLibrary')?.addEventListener('change', scheduleLibrariesSave);

    ['serverSettingsPort', 'serverSettingsHost', 'serverSettingsContext', 'serverSettingsIdle',
      'serverSettingsProfile', 'serverSettingsGpu'].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('change', scheduleGatewaySave);
      el.addEventListener('input', () => {
        if (id === 'serverSettingsPort' || id === 'serverSettingsHost') updateGatewayApiUrl();
      });
    });

    document.getElementById('serverSettingsPick')?.addEventListener('change', () => {
      renderGatewayPanel();
    });

    const modal = settingsModal();
    if (modal) {
      const observer = new MutationObserver(() => {
        if (!modal.classList.contains('open')) stopPolling();
      });
      observer.observe(modal, { attributes: true, attributeFilter: ['class'] });
    }
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashSettingsLive = {
    showPanel,
    openSettings,
    refresh: fetchHardware,
    addLibrariesFromScan,
    addLibraryFromBrowse,
    importLibraryAndAdd,
    importLibrariesAndAdd,
  };
})();
