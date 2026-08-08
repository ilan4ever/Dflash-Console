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
  const DEFAULT_PANEL = 'app-settings';
  const PANEL_ALIASES = {
    'gw-network': 'gw-engines',
    'gw-behavior': 'gw-engines',
    'gw-preset': 'gw-engines',
    'ws-checkpoints': 'ws-checkpoints',
  };
  const HW_PANELS = new Set(['hw-system', 'hw-gpus', 'hw-strategy', 'hw-live']);
  const GW_PANELS = new Set(['gw-engines']);
  const HARDWARE_PANELS = new Set(['ws-checkpoints', 'ws-locations', ...HW_PANELS]);
  const HARDWARE_TIMEOUT_MS = 30000;
  let hardwareLoadInFlight = null;
  let hardwareLoadError = null;

  function normalizePanelId(id) {
    const panelId = String(id || '').trim() || DEFAULT_PANEL;
    return PANEL_ALIASES[panelId] || panelId;
  }

  const STRATEGY_HINTS = {
    single_largest: 'Use one GPU — the fastest/largest card (RTX 4090). No layer-split.',
    split_evenly: 'Spread model layers evenly across GPUs (usually much slower over PCIe).',
    split_by_vram: 'Split large models across GPUs in proportion to VRAM.',
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function activePanelId() {
    const panel = document.querySelector('.lm-settings-panel.active');
    return panel?.dataset.settingsPanel || DEFAULT_PANEL;
  }

  function showPanel(id, { persist = true } = {}) {
    const panelId = normalizePanelId(id || DEFAULT_PANEL);
    document.querySelectorAll('.lm-settings-nav-item[data-settings-panel]').forEach((item) => {
      item.classList.toggle('active', item.dataset.settingsPanel === panelId);
    });
    document.querySelectorAll('.lm-settings-panel').forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.settingsPanel === panelId);
    });
    if (persist) {
      window.DFlashUiLayout?.setString?.('settings_panel', panelId);
      window.DFlashShell?.syncHash?.();
    }
    if (HW_PANELS.has(panelId)) renderHardwareForPanel(panelId);
    if (GW_PANELS.has(panelId)) renderGatewayPanel();
    if (panelId === 'int-mcp') void renderMcpPanel();
    if (panelId === 'rt-runtimes') void refreshRuntimesPanel();
    if (panelId === 'app-settings') void window.DFlashAppSettingsLive?.render?.();
    if (HARDWARE_PANELS.has(panelId)) {
      if (hardwareData) {
        if (panelId === 'ws-checkpoints' || panelId === 'ws-locations') renderWorkspacePaths();
      } else {
        showLoadingForPanel(panelId);
        void ensureSettingsData();
      }
    }
  }

  function openSettings(panelId = DEFAULT_PANEL) {
    showPanel(panelId);
    window.DFlashShell?.setView?.('settings');
  }

  function onViewEnter() {
    const panel = activePanelId();
    if (HW_PANELS.has(panel)) renderHardwareForPanel(panel);
    if (GW_PANELS.has(panel)) renderGatewayPanel();
    if (panel === 'int-mcp') void renderMcpPanel();
    if (panel === 'rt-runtimes') void refreshRuntimesPanel();
    if (panel === 'app-settings') void window.DFlashAppSettingsLive?.render?.();
    if (HARDWARE_PANELS.has(panel) && !hardwareData) showLoadingForPanel(panel);
    void ensureSettingsData().finally(() => startPolling());
  }

  function onViewLeave() {
    stopPolling();
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

  function showLoadingForPanel(panelId) {
    if (panelId === 'hw-system') {
      const el = document.getElementById('settingsHwSummary');
      if (el && !hardwareData) {
        el.innerHTML = '<p class="lm-setting-desc">Loading system info…</p>';
      }
    }
    if (panelId === 'hw-live') {
      const el = document.getElementById('settingsHwMonitor');
      if (el && !hardwareData) el.innerHTML = '<p class="lm-setting-desc">Loading live stats…</p>';
    }
    if (panelId === 'ws-checkpoints') {
      const list = document.getElementById('settingsModelLibraries');
      const downloadPick = document.getElementById('settingsDownloadLibrary');
      if (list && !hardwareData && !librariesDraft?.length) {
        list.innerHTML = '<p class="lm-setting-desc">Loading library folders…</p>';
      }
      if (downloadPick && !hardwareData) {
        window.ConsoleApi?.setSelectLoading?.(downloadPick, true, 'Loading…');
      }
    }
    if (panelId === 'hw-gpus') {
      const detect = document.getElementById('settingsGpuDetectLine');
      if (detect && !hardwareData) detect.textContent = 'Detecting GPUs…';
    }
  }

  function showHardwareLoadError(message) {
    hardwareLoadError = message || 'Could not load hardware info';
    const panel = activePanelId();
    if (panel === 'hw-system') {
      const el = document.getElementById('settingsHwSummary');
      if (el) {
        el.innerHTML = `<p class="lm-setting-desc lm-settings-load-err">${escapeHtml(hardwareLoadError)}</p>`;
      }
    }
    if (panel === 'ws-checkpoints') {
      const list = document.getElementById('settingsModelLibraries');
      if (list && !librariesDraft?.length) {
        list.innerHTML = `<p class="lm-setting-desc lm-settings-load-err">${escapeHtml(hardwareLoadError)}</p>`;
      }
    }
  }

  async function ensureSettingsData({ force = false } = {}) {
    if (hardwareData && !force) return hardwareData;
    if (hardwareLoadInFlight) return hardwareLoadInFlight;
    hardwareLoadError = null;
    hardwareLoadInFlight = fetchHardware({ retries: force ? 1 : 3 })
      .catch((err) => {
        showHardwareLoadError(err?.message || 'Could not load settings data');
        throw err;
      })
      .finally(() => {
        hardwareLoadInFlight = null;
      });
    return hardwareLoadInFlight;
  }

  function renderWorkspacePaths() {
    if (!hardwareData) return;
    const configPath = document.getElementById('settingsConfigPath');
    const dflashRoot = document.getElementById('settingsDflashRoot');
    const modelsDir = document.getElementById('settingsModelsDir');
    const logsDir = document.getElementById('settingsLogsDir');
    const presetsDir = document.getElementById('settingsPresetsDir');
    const uiPort = document.getElementById('settingsUiPort');
    if (configPath) configPath.textContent = hardwareData.config_path || '—';
    if (dflashRoot) dflashRoot.textContent = hardwareData.dflash_root || '—';
    if (modelsDir) modelsDir.textContent = hardwareData.models_dir || '—';
    if (logsDir) logsDir.textContent = hardwareData.logs_dir || '—';
    if (presetsDir) presetsDir.textContent = hardwareData.presets_dir || '—';
    if (uiPort) uiPort.textContent = hardwareData.ui_port ? `http://127.0.0.1:${hardwareData.ui_port}/` : '—';
    renderModelLibraries();
  }

  function downloadJson(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function pickJsonFile() {
    return new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json,application/json';
      input.addEventListener('change', () => {
        resolve(input.files?.[0] || null);
      });
      input.click();
    });
  }

  async function exportConfigFile() {
    try {
      const data = await api('/api/config');
      downloadJson('dflash-console-config.json', data.config || {});
      toast('Config exported');
    } catch (err) {
      toast(err.message, false);
    }
  }

  async function importConfigFile() {
    const file = await pickJsonFile();
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        toast('Invalid config file', false);
        return;
      }
      const ok = window.confirm('Replace your current console settings with this config file?');
      if (!ok) return;
      await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify(parsed),
      });
      toast('Config imported');
      librariesDraft = null;
      hardwareDraft = null;
      await fetchHardware();
      if (window.DFlashServerLive?.refresh) void window.DFlashServerLive.refresh();
      if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
    } catch (err) {
      toast(err.message || 'Could not import config', false);
    }
  }

  async function exportPresetFiles() {
    try {
      const data = await api('/api/presets/export');
      downloadJson('dflash-console-presets.json', { files: data.files || {} });
      toast(`Exported ${data.count || 0} preset file${data.count === 1 ? '' : 's'}`);
    } catch (err) {
      toast(err.message, false);
    }
  }

  async function importPresetFiles() {
    const file = await pickJsonFile();
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const files = parsed?.files && typeof parsed.files === 'object' ? parsed.files : parsed;
      if (!files || typeof files !== 'object' || Array.isArray(files)) {
        toast('Invalid presets file', false);
        return;
      }
      const names = Object.keys(files).filter((name) => String(name).toLowerCase().endsWith('.ini'));
      if (!names.length) {
        toast('No .ini preset files found in import', false);
        return;
      }
      const ok = window.confirm(`Import ${names.length} launch preset file${names.length === 1 ? '' : 's'}?`);
      if (!ok) return;
      const result = await api('/api/presets/import', {
        method: 'POST',
        body: JSON.stringify({ files }),
      });
      toast(`Imported ${result.written || 0} preset file${result.written === 1 ? '' : 's'}`);
    } catch (err) {
      toast(err.message || 'Could not import presets', false);
    }
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
    if (!el) return;
    if (!hardwareData) {
      el.innerHTML = hardwareLoadError
        ? `<p class="lm-setting-desc lm-settings-load-err">${escapeHtml(hardwareLoadError)}</p>`
        : '<p class="lm-setting-desc">Loading system info…</p>';
      return;
    }
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
    if (strategy) strategy.value = hardwareDraft.gpu_strategy || 'single_largest';
    if (hint) hint.textContent = STRATEGY_HINTS[strategy?.value || 'single_largest'] || STRATEGY_HINTS.single_largest;
    if (dedicated) dedicated.checked = hardwareDraft.limit_offload_dedicated_vram !== false;
    if (kv) kv.checked = hardwareDraft.offload_kv_cache_to_gpu !== false;
    renderGpuList();
    filling = false;
  }

  function readHardwareDraftFromForm() {
    if (!hardwareDraft) hardwareDraft = {};
    const strategy = document.getElementById('settingsGpuStrategy');
    hardwareDraft.gpu_strategy = strategy?.value || 'single_largest';
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

  // --- Speech & runtimes panel -------------------------------------------

  let runtimeDraft = [];
  let runtimeRefreshInFlight = null;

  async function loadRuntimeVoices(runtimeId) {
    try {
      const data = await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/voices`, { timeoutMs: 8000 });
      return Array.isArray(data?.voices) ? data.voices : [];
    } catch (_err) {
      return [];
    }
  }

  async function loadRuntimeSttModels() {
    try {
      const data = await api('/api/models', { timeoutMs: 20000 });
      return (Array.isArray(data?.models) ? data.models : []).filter((m) => {
        const name = String(m.filename || m.label || m.path || '').toLowerCase();
        return String(m.modality || '') === 'speech-to-text' || /whisper|faster-whisper/.test(name);
      });
    } catch (_err) {
      return [];
    }
  }

  async function refreshRuntimesPanel() {
    const listEl = document.getElementById('runtimeSettingsList');
    const summaryEl = document.getElementById('runtimeContentionSummary');
    if (!listEl) return;
    // Re-entrancy guard: the panel is refreshed from multiple hooks (nav,
    // showPanel, save), so coalesce concurrent calls into one run. Without
    // this, shared runtimeDraft gets interleaved pushes (duplicated cards).
    if (runtimeRefreshInFlight) return runtimeRefreshInFlight;
    const run = (async () => {
      listEl.innerHTML = '<p class="lm-setting-desc">Loading runtimes…</p>';
      try {
        const [runtimesData, manifestsData, contentionData, configData] = await Promise.all([
          api('/api/runtimes', { timeoutMs: 10000 }),
          api('/api/runtimes/manifests', { timeoutMs: 8000 }),
          api('/api/gpu/contention', { timeoutMs: 12000 }),
          api('/api/config', { timeoutMs: 8000 }),
        ]);
        const runtimes = (Array.isArray(runtimesData?.runtimes) ? runtimesData.runtimes : [])
          .filter((row) => row.kind === 'runtime');
        const manifests = {};
        for (const item of Array.isArray(manifestsData?.manifests) ? manifestsData.manifests : []) {
          manifests[item.runtime_id] = item.manifest;
        }
        const cfg = configData?.config || {};
        const stopToggle = document.getElementById('runtimeStopOthersToggle');
        const cpuToggle = document.getElementById('runtimeCpuWarnToggle');
        if (stopToggle) stopToggle.checked = cfg.runtime_stop_others_on_load === true;
        if (cpuToggle) cpuToggle.checked = cfg.cpu_slow_warn !== false;
        const draft = [];
        for (const row of runtimes) {
          const rt = {
            id: row.id,
            runtime_id: row.runtime_id,
            label: row.label || row.runtime_id,
            port: row.port || 0,
            device_policy: row.device_policy || 'auto',
            default_voice: row.default_voice || '',
            default_model: row.default_model || '',
            allow_cpu_fallback: row.allow_cpu_fallback !== false,
            vram_budget_mb: Number(row.vram_budget_mb) || 0,
            adapter_installed: row.adapter_installed === true,
            manifest: manifests[row.runtime_id] || null,
          };
          rt._voices = rt.runtime_id === 'piper' ? await loadRuntimeVoices(rt.runtime_id) : [];
          rt._models = rt.runtime_id === 'stt' ? await loadRuntimeSttModels() : [];
          draft.push(rt);
        }
        runtimeDraft = draft;
        if (!runtimeDraft.length) {
          listEl.innerHTML = '<p class="lm-setting-desc">No non-llama runtimes configured. Add a <code>runtimes[]</code> entry to config.json.</p>';
        } else {
          listEl.innerHTML = runtimeDraft.map((rt, index) => {
            const installedTag = rt.adapter_installed
              ? '<span class="lm-tag green">Installed</span>'
              : '<span class="lm-tag yellow">Not installed</span>';
            const voiceOptions = (rt._voices || []).map((v) => {
              const id = v.id || '';
              return `<option value="${escapeHtml(id)}" ${rt.default_voice === id ? 'selected' : ''}>${escapeHtml(v.label || id)}</option>`;
            }).join('');
            const modelOptions = (rt._models || []).map((m) => {
              const path = m.path || '';
              return `<option value="${escapeHtml(path)}" ${rt.default_model === path ? 'selected' : ''}>${escapeHtml(m.filename || m.label || path)}</option>`;
            }).join('');
            const defaultVoiceRow = rt.runtime_id === 'piper'
              ? `<div class="lm-setting-row">
                <div><strong>Default voice</strong><p class="lm-setting-desc">Piper voice used by the Playground Speak tab</p></div>
                <select class="lm-select" data-rt-field="default_voice" data-rt-index="${index}">
                  <option value="">Default (first)</option>
                  ${voiceOptions}
                </select>
              </div>`
              : '';
            const defaultModelRow = rt.runtime_id === 'stt'
              ? `<div class="lm-setting-row">
                <div><strong>Default STT model</strong><p class="lm-setting-desc">Whisper model used by the Playground Transcribe tab</p></div>
                <select class="lm-select" data-rt-field="default_model" data-rt-index="${index}">
                  <option value="">Default (first)</option>
                  ${modelOptions}
                </select>
              </div>`
              : '';
            return `
            <div class="lm-runtime-settings-card">
              <div class="lm-runtime-settings-head">
                <strong>${escapeHtml(rt.label)}</strong>
                <code>${escapeHtml(rt.runtime_id)}</code>
                ${installedTag}
              </div>
              <div class="lm-setting-row">
                <div><strong>Device policy</strong><p class="lm-setting-desc">auto · gpu · cpu</p></div>
                <select class="lm-select" data-rt-field="device_policy" data-rt-index="${index}">
                  <option value="auto" ${rt.device_policy === 'auto' ? 'selected' : ''}>Auto</option>
                  <option value="gpu" ${rt.device_policy === 'gpu' ? 'selected' : ''}>GPU</option>
                  <option value="cpu" ${rt.device_policy === 'cpu' ? 'selected' : ''}>CPU</option>
                </select>
              </div>
              ${defaultVoiceRow}
              ${defaultModelRow}
              <div class="lm-setting-row">
                <div><strong>Allow CPU fallback</strong><p class="lm-setting-desc">Fall back to CPU when GPU memory is tight</p></div>
                <label class="lm-toggle"><input type="checkbox" data-rt-field="allow_cpu_fallback" data-rt-index="${index}" ${rt.allow_cpu_fallback ? 'checked' : ''}><span class="lm-toggle-track"></span></label>
              </div>
              <div class="lm-setting-row">
                <div><strong>VRAM budget (MB)</strong><p class="lm-setting-desc">0 = unlimited</p></div>
                <input type="number" class="lm-num" min="0" step="256" data-rt-field="vram_budget_mb" data-rt-index="${index}" value="${rt.vram_budget_mb}">
              </div>
              ${rt.manifest ? `<div class="lm-setting-desc">Manifest: ${escapeHtml(rt.manifest.binary || rt.runtime_id)} · version ${escapeHtml(rt.manifest.version)}</div>` : ''}
            </div>`;
          }).join('');
        }
        if (summaryEl) {
          const c = contentionData || {};
          const rec = c.recommendation || 'none';
          const recLabel = rec === 'stop-others'
            ? 'Console runtimes hold VRAM — stop others before loading'
            : rec === 'warn-external'
              ? 'External apps hold VRAM — warn by name'
              : 'No significant GPU contention';
          const running = (c.console_runtimes || []).filter((r) => r.running).map((r) => r.label).join(', ') || 'none';
          const external = (c.external || []).map((e) => e.title).slice(0, 4).join(', ') || 'none';
          summaryEl.innerHTML = `<span class="lm-tag ${rec === 'none' ? 'green' : 'yellow'}">${escapeHtml(recLabel)}</span>
            <div class="lm-setting-desc">Console: ${escapeHtml(running)} · External: ${escapeHtml(external)}</div>`;
        }
      } catch (err) {
        listEl.innerHTML = `<p class="lm-setting-desc">${escapeHtml(err.message || 'Could not load runtimes.')}</p>`;
      }
    })();
    runtimeRefreshInFlight = run;
    try {
      return await run;
    } finally {
      if (runtimeRefreshInFlight === run) runtimeRefreshInFlight = null;
    }
  }

  async function persistRuntimes() {
    document.querySelectorAll('[data-rt-field]').forEach((el) => {
      const index = Number(el.dataset.rtIndex);
      const field = el.dataset.rtField;
      const row = runtimeDraft[index];
      if (!row) return;
      if (field === 'device_policy') row.device_policy = el.value;
      else if (field === 'default_voice') row.default_voice = el.value;
      else if (field === 'default_model') row.default_model = el.value;
      else if (field === 'allow_cpu_fallback') row.allow_cpu_fallback = el.checked;
      else if (field === 'vram_budget_mb') row.vram_budget_mb = Math.max(0, Number(el.value) || 0);
    });
    const payload = runtimeDraft.map((row) => ({
      id: row.id,
      runtime_id: row.runtime_id,
      label: row.label,
      port: row.port,
      device_policy: row.device_policy,
      default_voice: row.default_voice,
      default_model: row.default_model,
      allow_cpu_fallback: row.allow_cpu_fallback,
      vram_budget_mb: row.vram_budget_mb,
    }));
    const body = { runtimes: payload };
    const stopToggle = document.getElementById('runtimeStopOthersToggle');
    const cpuToggle = document.getElementById('runtimeCpuWarnToggle');
    if (stopToggle) body.runtime_stop_others_on_load = stopToggle.checked;
    if (cpuToggle) body.cpu_slow_warn = cpuToggle.checked;
    try {
      const result = await api('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        timeoutMs: 10000,
      });
      toast(result?.success ? 'Runtime settings saved.' : 'Could not save runtime settings.', !!result?.success);
    } catch (err) {
      toast(err.message || 'Could not save runtime settings.', false);
    }
    void refreshRuntimesPanel();
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

  async function fetchHardware({ silent = false, retries = 1 } = {}) {
    let lastError = null;
    for (let attempt = 0; attempt < retries; attempt += 1) {
      try {
        const [stats, data] = await Promise.all([
          api('/api/system-stats', { timeoutMs: 15000 }).catch(() => ({})),
          api('/api/hardware', { timeoutMs: HARDWARE_TIMEOUT_MS }),
        ]);
        hardwareLoadError = null;
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
        return data;
      } catch (err) {
        lastError = err;
        if (attempt < retries - 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 800 * (attempt + 1)));
        }
      }
    }
    throw lastError || new Error('Could not load hardware info');
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
      `Strategy: ${hardwareDraft?.gpu_strategy || 'single_largest'}`,
    ];
    navigator.clipboard.writeText(lines.join('\n')).then(() => toast('Hardware info copied'));
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      if (document.body.dataset.activeView !== 'settings') return;
      void fetchHardware({ retries: 1 }).then(() => {
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

  function bind() {
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
    document.getElementById('runtimeSettingsSave')?.addEventListener('click', () => void persistRuntimes());
    document.getElementById('settingsAddLibrary')?.addEventListener('click', openLibraryBrowse);
    document.getElementById('settingsScanLibrary')?.addEventListener('click', openLibraryScan);
    document.getElementById('settingsDownloadLibrary')?.addEventListener('change', scheduleLibrariesSave);
    document.getElementById('settingsExportConfig')?.addEventListener('click', () => void exportConfigFile());
    document.getElementById('settingsImportConfig')?.addEventListener('click', () => void importConfigFile());
    document.getElementById('settingsExportPresets')?.addEventListener('click', () => void exportPresetFiles());
    document.getElementById('settingsImportPresets')?.addEventListener('click', () => void importPresetFiles());

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
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    void ensureSettingsData().catch(() => {});
  });

  window.DFlashSettingsLive = {
    showPanel,
    activePanelId,
    openSettings,
    onViewEnter,
    onViewLeave,
    refresh: fetchHardware,
    refreshRuntimesPanel,
    addLibrariesFromScan,
    addLibraryFromBrowse,
    importLibraryAndAdd,
    importLibrariesAndAdd,
  };
})();
