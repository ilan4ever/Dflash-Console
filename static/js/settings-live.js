/** Settings modal — nested nav, workspace / compute / engines */
(function () {
  const { api, toast } = window.ConsoleApi;

  let hardwareData = null;
  let hardwareDraft = null;
  let librariesDraft = null;
  let librariesSaveTimer = null;
  let downloadSettingsDraft = null;
  let downloadSettingsSaveTimer = null;
  let downloadBenchmarkInFlight = null;
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
  let settingsSearchIndex = [];
  let componentsRefreshInFlight = null;
  let componentsPollTimer = null;

  function normalizePanelId(id) {
    const panelId = String(id || '').trim() || DEFAULT_PANEL;
    return PANEL_ALIASES[panelId] || panelId;
  }

  function hardwareDirty() {
    return Boolean(saveTimer);
  }

  function applyHardwareSettingsFromServer(data) {
    const incoming = { ...(data?.hardware_settings || {}) };
    delete incoming.guardrails;
    delete incoming.guardrails_max_model_gb;
    if (!hardwareDirty() || !hardwareDraft) {
      hardwareDraft = incoming;
    }
  }

  function gpuStrategyInputs() {
    return [...document.querySelectorAll('input[name="settingsGpuStrategy"]')];
  }

  function gpuStrategyValue() {
    return gpuStrategyInputs().find((el) => el.checked)?.value || 'single_largest';
  }

  function setGpuStrategyValue(value) {
    const next = String(value || 'single_largest');
    gpuStrategyInputs().forEach((el) => {
      el.checked = el.value === next;
    });
  }

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
    if (panelId === 'dl-components') void refreshComponentsPanel();
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
    if (panel === 'dl-components') void refreshComponentsPanel();
    if (panel === 'app-settings') void window.DFlashAppSettingsLive?.render?.();
    void refreshComponentsPanel({ quiet: true });
    if (HARDWARE_PANELS.has(panel) && !hardwareData) showLoadingForPanel(panel);
    void ensureSettingsData().finally(() => startPolling());
  }

  function onViewLeave() {
    stopPolling();
    stopComponentsPolling();
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
    if (uiPort) uiPort.textContent = `${window.location.protocol}//${window.location.host}/`;
    const sharedUrl = document.getElementById('appSettingsConsoleUrl');
    if (sharedUrl) sharedUrl.textContent = `${window.location.protocol}//${window.location.host}/`;
    const sharedConfig = document.getElementById('appSettingsConfigPath');
    if (sharedConfig) sharedConfig.textContent = hardwareData.config_path || '—';
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
    renderDownloadSettings();
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
            overwrite: false,
          }),
        });
        if (data?.exists) {
          const existing = data.existing_path || data.filename || 'the Console library';
          const overwrite = window.confirm(
            `${data.error || 'This model is already in the DFlash Console library.'}\n\nExisting: ${existing}\n\nReplace the existing copy?`,
          );
          if (!overwrite) return false;
          const retry = await api('/api/model-libraries/import', {
            method: 'POST',
            body: JSON.stringify({
              path: entry.path,
              preset: entry.preset || 'dflash',
              mode: importMode,
              overwrite: true,
            }),
          });
          Object.assign(data, retry);
        }
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

  function renderDownloadSettings() {
    const pick = document.getElementById('settingsDownloadConnections');
    if (!pick) return;
    if (!downloadSettingsDraft) {
      downloadSettingsDraft = { ...(hardwareData?.download_settings || { parallel_connections: 4 }) };
    }
    const value = String(Math.max(1, Math.min(8, Number(downloadSettingsDraft.parallel_connections) || 4)));
    if (pick.value !== value) pick.value = value;
    pick.disabled = false;
    pick.classList.remove('is-loading');
    window.DFlashSelectTheme?.syncSelect?.(pick);
  }

  function readDownloadSettingsDraftFromForm() {
    const pick = document.getElementById('settingsDownloadConnections');
    if (!pick || !downloadSettingsDraft) return;
    const next = Math.max(1, Math.min(8, Number(pick.value) || 4));
    downloadSettingsDraft.parallel_connections = next;
  }

  async function persistDownloadSettings() {
    readDownloadSettingsDraftFromForm();
    const result = await api('/api/download-settings', {
      method: 'PATCH',
      body: JSON.stringify(downloadSettingsDraft),
      timeoutMs: 15000,
    });
    downloadSettingsDraft = { ...(result.download_settings || downloadSettingsDraft) };
    renderDownloadSettings();
    toast('Download connection setting saved');
  }

  function scheduleDownloadSettingsSave() {
    if (filling) return;
    readDownloadSettingsDraftFromForm();
    if (downloadSettingsSaveTimer) clearTimeout(downloadSettingsSaveTimer);
    downloadSettingsSaveTimer = window.setTimeout(() => {
      downloadSettingsSaveTimer = null;
      persistDownloadSettings().catch((err) => toast(err.message || 'Could not save download settings', false));
    }, 450);
  }

  function renderDownloadBenchmarkResults(payload) {
    const el = document.getElementById('settingsDownloadBenchmarkResults');
    if (!el) return;
    if (!payload?.results?.length) {
      el.classList.add('hidden');
      el.innerHTML = '';
      return;
    }
    const rows = payload.results.map((row) => {
      if (!row.success) {
        return `<div class="lm-download-benchmark-row is-error">${escapeHtml(String(row.connections))} connections · failed · ${escapeHtml(row.error || 'error')}</div>`;
      }
      const best = Number(payload.best_connections) === Number(row.connections);
      return `<div class="lm-download-benchmark-row${best ? ' is-best' : ''}">${escapeHtml(String(row.connections))} connection${row.connections === 1 ? '' : 's'} · ${escapeHtml(String(row.mbps))} MiB/s${best ? ' · fastest' : ''}</div>`;
    }).join('');
    const summary = payload.gain_vs_single_pct != null
      ? `<p class="lm-setting-desc">Best: ${escapeHtml(String(payload.best_connections))} connections at ${escapeHtml(String(payload.best_mbps))} MiB/s (${escapeHtml(String(payload.gain_vs_single_pct))}% vs single stream). Sample: ${escapeHtml(String(payload.test_mib))} MiB.</p>`
      : `<p class="lm-setting-desc">Sample: ${escapeHtml(String(payload.test_mib))} MiB from Hugging Face.</p>`;
    el.innerHTML = `${summary}<div class="lm-download-benchmark-list">${rows}</div>`;
    el.classList.remove('hidden');
  }

  async function runDownloadBenchmark() {
    if (downloadBenchmarkInFlight) return downloadBenchmarkInFlight;
    const btn = document.getElementById('settingsDownloadBenchmarkBtn');
    const resultsEl = document.getElementById('settingsDownloadBenchmarkResults');
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Testing…';
    }
    if (resultsEl) {
      resultsEl.classList.remove('hidden');
      resultsEl.innerHTML = '<p class="lm-setting-desc">Running speed test — this may take up to a minute…</p>';
    }
    downloadBenchmarkInFlight = api('/api/download-settings/benchmark', {
      method: 'POST',
      body: JSON.stringify({ test_mib: 32 }),
      timeoutMs: 300000,
    }).then((data) => {
      renderDownloadBenchmarkResults(data);
      return data;
    }).catch((err) => {
      if (resultsEl) {
        resultsEl.classList.remove('hidden');
        resultsEl.innerHTML = `<p class="lm-setting-desc lm-settings-load-err">${escapeHtml(err.message || 'Benchmark failed')}</p>`;
      }
      toast(err.message || 'Download benchmark failed', false);
      throw err;
    }).finally(() => {
      downloadBenchmarkInFlight = null;
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Run test';
      }
    });
    return downloadBenchmarkInFlight;
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
    const dedicated = document.getElementById('settingsLimitDedicatedVram');
    const kv = document.getElementById('settingsOffloadKvGpu');
    setGpuStrategyValue(hardwareDraft.gpu_strategy || 'single_largest');
    if (dedicated) dedicated.checked = hardwareDraft.limit_offload_dedicated_vram !== false;
    if (kv) kv.checked = hardwareDraft.offload_kv_cache_to_gpu !== false;
    renderGpuList();
    filling = false;
  }

  function readHardwareDraftFromForm() {
    if (!hardwareDraft) hardwareDraft = {};
    hardwareDraft.gpu_strategy = gpuStrategyValue();
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
    void renderGatewayConfig();
  }

  async function renderGatewayConfig() {
    const portEl = document.getElementById('settingsGatewayPort');
    const serverEl = document.getElementById('settingsGatewayServer');
    if (!portEl || !serverEl) return;
    try {
      const [cfgData, serversData] = await Promise.all([
        api('/api/config', { timeoutMs: 8000 }),
        api('/api/servers/profiles', { timeoutMs: 10000 }),
      ]);
      const cfg = cfgData?.config || {};
      const servers = (Array.isArray(serversData?.all_servers) ? serversData.all_servers : [])
        .filter((s) => s.enabled !== false);
      portEl.value = cfg.gateway_port || 8001;
      const current = String(cfg.gateway_server_id || '');
      serverEl.innerHTML = '<option value="">Auto (first enabled)</option>' +
        servers.map((s) => `<option value="${escapeHtml(s.id)}" ${current === s.id ? 'selected' : ''}>${escapeHtml(s.label || s.id)}</option>`).join('');
      updateGatewayUrl();
    } catch (_err) { /* keep defaults */ }
  }

  function updateGatewayUrl() {
    const urlEl = document.getElementById('settingsGatewayUrl');
    const portEl = document.getElementById('settingsGatewayPort');
    if (urlEl) urlEl.textContent = `http://127.0.0.1:${portEl?.value || 8001}/v1`;
  }

  async function saveGatewayConfig() {
    const portEl = document.getElementById('settingsGatewayPort');
    const serverEl = document.getElementById('settingsGatewayServer');
    if (!portEl) return;
    const port = Math.max(1, Math.min(65535, Number(portEl.value) || 8001));
    const body = { gateway_port: port };
    if (serverEl) body.gateway_server_id = serverEl.value;
    try {
      const result = await api('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        timeoutMs: 10000,
      });
      toast(result?.success ? 'Gateway settings saved — restart applies a new port.' : 'Could not save gateway settings.', !!result?.success);
      if (result?.success) updateGatewayUrl();
    } catch (err) {
      toast(err.message || 'Could not save gateway settings.', false);
    }
  }

  function buildMcpJson(servers, gatewayUrl) {
    const engines = (servers || []).map((server) => ({
      id: server.id,
      label: server.label || server.id,
      openai_base_url: gatewayUrl,
      model: server.id || '',
      enabled: server.enabled !== false,
    }));
    return {
      mcpServers: {},
      dflashConsole: {
        note: 'MCP host is not active in DFlash Console yet. Point OpenAI-compatible clients at openai_base_url (the Console OpenAI gateway); send "model" = the engine id.',
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
      const [data, configData] = await Promise.all([
        api('/api/servers'),
        api('/api/config', { timeoutMs: 8000 }).catch(() => ({ config: {} })),
      ]);
      const gatewayUrl = `http://127.0.0.1:${Number(configData?.config?.gateway_port) || 8001}/v1`;
      const servers = (data.all_servers || data.servers || []).filter((row) => row.enabled !== false);
      if (!servers.length) {
        listEl.innerHTML = '<p class="lm-setting-desc">No engine profiles configured.</p>';
      } else {
        listEl.innerHTML = servers.map((server) => {
          const url = gatewayUrl;
          const status = server.status === 'loaded' ? 'Loaded' : server.running ? 'Running' : 'Stopped';
          const statusClass = server.status === 'loaded' || server.running ? 'green' : 'dim';
          return `
            <div class="lm-mcp-engine-card">
              <div><strong>${escapeHtml(server.label || server.id)}</strong> <span class="lm-tag ${statusClass}">${escapeHtml(status)}</span></div>
              <code>${escapeHtml(url)}</code> <span class="lm-mcp-model">model: ${escapeHtml(server.id || '')}</span>
            </div>`;
        }).join('');
      }
      jsonEl.textContent = JSON.stringify(buildMcpJson(servers, gatewayUrl), null, 2);
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
            compute_type: row.compute_type || 'auto',
            language: row.language || '',
            task: row.task || 'transcribe',
            beam_size: Number(row.beam_size) || 5,
            vad_filter: row.vad_filter === true,
            temperature: Number(row.temperature ?? 0),
            cpu_threads: Number(row.cpu_threads) || 0,
            num_workers: Number(row.num_workers) || 0,
            running: row.running === true,
            active_model: row.active_model || '',
            active_device: row.active_device || '',
            active_compute_type: row.active_compute_type || '',
            cfg_scale: Number(row.cfg_scale ?? 1.5),
            ddpm_steps: Number(row.ddpm_steps) || 5,
          };
          rt._voices = (rt.runtime_id === 'piper' || rt.runtime_id === 'vibevoice') ? await loadRuntimeVoices(rt.runtime_id) : [];
          rt._models = (rt.runtime_id === 'stt' || rt.runtime_id === 'faster-whisper') ? await loadRuntimeSttModels() : [];
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
            const defaultVoiceRow = (rt.runtime_id === 'piper' || rt.runtime_id === 'vibevoice')
              ? `<div class="lm-setting-row">
                <div><strong>Default voice</strong><p class="lm-setting-desc">${rt.runtime_id === 'vibevoice' ? 'VibeVoice voice preset (speaker)' : 'Piper voice used by the Playground Speak tab'}</p></div>
                <select class="lm-select" data-rt-field="default_voice" data-rt-index="${index}">
                  <option value="">Default (first)</option>
                  ${voiceOptions}
                </select>
              </div>`
              : '';
            const defaultModelRow = (rt.runtime_id === 'stt' || rt.runtime_id === 'faster-whisper')
              ? `<div class="lm-setting-row">
                <div><strong>Default STT model</strong><p class="lm-setting-desc">Whisper model used by the Playground Transcribe tab</p></div>
                <select class="lm-select" data-rt-field="default_model" data-df-model-filter="1" data-rt-index="${index}">
                  <option value="">Default (first)</option>
                  ${modelOptions}
                </select>
              </div>`
              : '';
            const sttSettingsRows = rt.runtime_id === 'faster-whisper'
              ? `<div class="lm-setting-row">
                <div><strong>Compute type</strong><p class="lm-setting-desc">Precision for the model (GPU: float16 / int8_float16; CPU: int8)</p></div>
                <select class="lm-select" data-rt-field="compute_type" data-rt-index="${index}">
                  <option value="auto" ${rt.compute_type === 'auto' ? 'selected' : ''}>Auto</option>
                  <option value="float16" ${rt.compute_type === 'float16' ? 'selected' : ''}>float16 (GPU)</option>
                  <option value="int8_float16" ${rt.compute_type === 'int8_float16' ? 'selected' : ''}>int8_float16 (GPU)</option>
                  <option value="int8" ${rt.compute_type === 'int8' ? 'selected' : ''}>int8 (CPU)</option>
                  <option value="float32" ${rt.compute_type === 'float32' ? 'selected' : ''}>float32</option>
                </select>
              </div>
              <div class="lm-setting-row">
                <div><strong>Language</strong><p class="lm-setting-desc">Leave empty for auto-detect (e.g. en, he, fr)</p></div>
                <input type="text" class="lm-input" data-rt-field="language" data-rt-index="${index}" value="${escapeHtml(rt.language)}" placeholder="auto">
              </div>
              <div class="lm-setting-row">
                <div><strong>Task</strong><p class="lm-setting-desc">Transcribe (as spoken) or translate to English</p></div>
                <select class="lm-select" data-rt-field="task" data-rt-index="${index}">
                  <option value="transcribe" ${rt.task === 'translate' ? '' : 'selected'}>Transcribe</option>
                  <option value="translate" ${rt.task === 'translate' ? 'selected' : ''}>Translate to English</option>
                </select>
              </div>
              <div class="lm-setting-row">
                <div><strong>Beam size</strong><p class="lm-setting-desc">Search width — higher is more accurate but slower (1–16)</p></div>
                <input type="number" class="lm-num" min="1" max="16" data-rt-field="beam_size" data-rt-index="${index}" value="${rt.beam_size}">
              </div>
              <div class="lm-setting-row">
                <div><strong>VAD filter</strong><p class="lm-setting-desc">Skip silent segments with a voice-activity detector</p></div>
                <label class="lm-toggle"><input type="checkbox" data-rt-field="vad_filter" data-rt-index="${index}" ${rt.vad_filter ? 'checked' : ''}><span class="lm-toggle-track"></span></label>
              </div>
              <div class="lm-setting-row">
                <div><strong>CPU threads</strong><p class="lm-setting-desc">0 = faster-whisper default</p></div>
                <input type="number" class="lm-num" min="0" data-rt-field="cpu_threads" data-rt-index="${index}" value="${rt.cpu_threads}">
              </div>`
              : '';
            const sttControls = (rt.runtime_id === 'stt' || rt.runtime_id === 'faster-whisper' || rt.runtime_id === 'vibevoice' || rt.runtime_id === 'transformers' || rt.runtime_id === 'vllm')
              ? `<div class="lm-setting-row">
                  <div>
                    <strong>Status</strong>
                    <p class="lm-setting-desc">${rt.running
                      ? `Running · ${escapeHtml(String(rt.active_model).split(/[\\/]/).pop() || 'model')} · ${escapeHtml(rt.active_device || '')}${rt.active_compute_type ? ` ${escapeHtml(rt.active_compute_type)}` : ''}`
                      : 'Not running — load a model to start it'}</p>
                  </div>
                  <div class="lm-setting-actions">
                    <button class="lm-btn ghost small" type="button" data-action="stt-unload" data-rt-index="${index}" ${rt.running ? '' : 'disabled'}>Unload</button>
                    <button class="lm-btn primary small" type="button" data-action="stt-load" data-rt-index="${index}" ${rt.adapter_installed ? '' : 'disabled'}>${rt.running ? 'Reload' : 'Load model'}</button>
                  </div>
                </div>`
              : '';
            const vibevoiceRows = rt.runtime_id === 'vibevoice'
              ? `<div class="lm-setting-row">
                <div><strong>CFG scale</strong><p class="lm-setting-desc">Classifier-free guidance for speech diffusion (higher = more expressive)</p></div>
                <input type="number" class="lm-num" min="0.5" max="10" step="0.5" data-rt-field="cfg_scale" data-rt-index="${index}" value="${rt.cfg_scale}">
              </div>
              <div class="lm-setting-row">
                <div><strong>DDPM steps</strong><p class="lm-setting-desc">Diffusion inference steps — higher is better quality, slower (1–20)</p></div>
                <input type="number" class="lm-num" min="1" max="20" data-rt-field="ddpm_steps" data-rt-index="${index}" value="${rt.ddpm_steps}">
              </div>`
              : '';
            return `
            <div class="lm-runtime-settings-card">
              <div class="lm-runtime-settings-head">
                <strong>${escapeHtml(rt.label)}</strong>
                <code>${escapeHtml(rt.runtime_id)}</code>
                ${installedTag}
              </div>
              ${sttControls}
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
              ${sttSettingsRows}
              ${vibevoiceRows}
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

  // Load or unload an STT runtime (whisper.cpp / faster-whisper) from the
  // Speech & runtimes panel. Uses the runtime's default_model when set.
  async function runSttAction(index, action) {
    const rt = runtimeDraft[index];
    if (!rt) return;
    const runtimeId = rt.runtime_id;
    try {
      if (action === 'unload') {
        await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/unload`, { method: 'POST', timeoutMs: 20000 });
        toast(`${rt.label} unloaded`);
      } else {
        let path = rt.default_model || (Array.isArray(rt._models) && rt._models[0]?.path) || '';
        const body = path ? JSON.stringify({ path }) : undefined;
        await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/load`, {
          method: 'POST',
          body,
          timeoutMs: 120000,
        });
        toast(`${rt.label} model loaded`);
      }
      void refreshRuntimesPanel();
    } catch (err) {
      toast(err.message || 'STT action failed', false);
    }
  }

  function componentStatusTag(row) {
    const status = String(row?.status || '');
    if (row?.install_mode === 'bundled') {
      if (row?.installed) return '<span class="lm-tag gray">Included</span>';
      return '<span class="lm-tag yellow">Missing</span>';
    }
    if (status === 'installed') return '<span class="lm-tag green">Installed</span>';
    if (status === 'installing') return '<span class="lm-tag blue">Installing…</span>';
    if (status === 'update_available') return '<span class="lm-tag gold">Update available</span>';
    if (status === 'error') return '<span class="lm-tag red">Install failed</span>';
    if (status === 'not_installed') return '<span class="lm-tag yellow">Not installed</span>';
    return '<span class="lm-tag">—</span>';
  }

  function componentStatusLine(row) {
    const status = String(row?.status || '');
    if (row?.install_mode === 'bundled') {
      if (row?.installed) return 'Part of this DFlash Console install — update via app releases, not uninstall here.';
      return 'Expected bundle missing — repair by reinstalling DFlash Console.';
    }
    if (status === 'installed') return 'Ready — pick this engine on Engines, Models, or Playground, then Load.';
    if (status === 'installing') {
      const pct = row?.install_progress != null ? ` (${Math.round(Number(row.install_progress))}%)` : '';
      return `Downloading and installing…${pct}`;
    }
    if (status === 'update_available') return 'A newer download is available — click Update / repair.';
    if (status === 'error') return row?.install_error || 'Install failed — try again.';
    if (status === 'not_installed') return 'Not on this PC yet — click Install to download.';
    return '—';
  }

  function componentCardKindLabel(row) {
    const cat = String(row?.category || '');
    if (cat === 'speech') return 'Speech runtime';
    if (row?.id === 'dflash-gguf') return 'Core engine';
    return 'LLM engine';
  }

  function renderComponentCard(row) {
    const isExternal = row?.install_mode === 'on_demand';
    const kind = componentCardKindLabel(row);
    const actions = isExternal
      ? `<div class="lm-component-card-actions">
          <div class="lm-component-card-actions-row">
            ${componentInstallControls(row)}
          </div>
          ${componentUninstallRow(row)}
        </div>`
      : '';
    return `<article class="lm-component-card ${isExternal ? 'is-external' : 'is-bundled'}" data-component-id="${escapeHtml(row.id)}">
      <div class="lm-component-card-head">
        <div class="lm-component-card-identity">
          <span class="lm-component-card-kind">${escapeHtml(kind)}</span>
          <strong class="lm-component-card-title">${escapeHtml(row.label || row.id)}</strong>
        </div>
        ${componentStatusTag(row)}
      </div>
      <p class="lm-setting-desc lm-component-card-desc">${escapeHtml(row.description || '')}</p>
      <p class="lm-component-card-status">${escapeHtml(componentStatusLine(row))}</p>
      ${actions}
    </article>`;
  }

  function renderComponentsSection(title, desc, rows, { bundled = false } = {}) {
    if (!rows.length) return '';
    const cards = rows.map((row) => renderComponentCard(row)).join('');
    return `<div class="lm-components-section${bundled ? ' is-bundled' : ' is-external'}">
      <div class="lm-components-section-head">
        <strong>${escapeHtml(title)}</strong>
        <p class="lm-setting-desc">${escapeHtml(desc)}</p>
      </div>
      <div class="lm-components-cards">${cards}</div>
    </div>`;
  }

  function renderComponentsList(components) {
    const listEl = document.getElementById('componentsHubList');
    if (!listEl) return;
    const rows = Array.isArray(components) ? components : [];
    if (!rows.length) {
      listEl.innerHTML = '<p class="lm-setting-desc">No components found.</p>';
      return;
    }
    const external = rows.filter((row) => row?.install_mode === 'on_demand');
    const bundled = rows.filter((row) => row?.install_mode !== 'on_demand');
    const bundledSorted = bundled.slice().sort((a, b) => {
      const order = { llm_engine: 0, speech: 1 };
      const ao = order[a?.category] ?? 9;
      const bo = order[b?.category] ?? 9;
      if (ao !== bo) return ao - bo;
      return String(a?.label || '').localeCompare(String(b?.label || ''));
    });
    const externalHtml = renderComponentsSection(
      'Extra downloads',
      'Large engines you download after install (vLLM, Transformers). Install and uninstall here.',
      external,
    );
    const bundledHtml = renderComponentsSection(
      'Included with DFlash Console',
      'GGUF engines and speech runtimes ship with the app installer. No separate uninstall — update via Desktop app releases.',
      bundledSorted,
      { bundled: true },
    );
    const divider = external.length && bundledSorted.length
      ? '<div class="lm-components-divider" role="separator"><span>Bundled with app</span></div>'
      : '';
    listEl.innerHTML = `${externalHtml}${divider}${bundledHtml}`;
  }

  function componentInstallControls(row) {
    if (row?.install_mode !== 'on_demand' || !row?.runtime_id) return '';
    const rid = row.runtime_id;
    const disabled = row?.status === 'installing';
    const label = row?.status === 'update_available' || row?.installed ? 'Update / repair' : 'Install';
    if (rid === 'vllm') {
      return `<div class="lm-setting-actions">
        <select class="lm-select small" data-component-option="backend" data-component-id="${escapeHtml(rid)}" title="Install path">
          <option value="auto" selected>Auto (Windows, then WSL)</option>
          <option value="native">Windows only</option>
          <option value="wsl">WSL only</option>
        </select>
        <button class="lm-btn primary small" type="button" data-action="component-install" data-component-id="${escapeHtml(rid)}" ${disabled ? 'disabled' : ''}>${escapeHtml(label)}</button>
      </div>`;
    }
    if (rid === 'transformers') {
      return `<div class="lm-setting-actions">
        <select class="lm-select small" data-component-option="torch_variant" data-component-id="${escapeHtml(rid)}" title="PyTorch build">
          <option value="auto" selected>Auto (GPU if available)</option>
          <option value="cuda">CUDA</option>
          <option value="cpu">CPU only</option>
        </select>
        <button class="lm-btn primary small" type="button" data-action="component-install" data-component-id="${escapeHtml(rid)}" ${disabled ? 'disabled' : ''}>${escapeHtml(label)}</button>
      </div>`;
    }
    return '';
  }

  function componentUninstallRow(row) {
    if (row?.install_mode !== 'on_demand' || !row?.runtime_id || !row?.installed) return '';
    if (row?.status === 'installing') return '';
    const rid = row.runtime_id;
    return `<div class="lm-component-uninstall-row">
      <button class="lm-btn ghost small lm-component-uninstall-btn" type="button" data-action="component-uninstall" data-component-id="${escapeHtml(rid)}">Uninstall download</button>
    </div>`;
  }

  function renderComponentsDownloads(jobs) {
    const el = document.getElementById('componentsDownloadsList');
    if (!el) return;
    const list = Array.isArray(jobs) ? jobs : [];
    if (!list.length) {
      el.innerHTML = '<p class="lm-setting-desc">No downloads in progress. Browse the Model catalog to download GGUF or SafeTensors models.</p>';
      return;
    }
    el.innerHTML = list.map((job) => {
      const label = escapeHtml(job.repo_id || job.filename || job.job_id || 'Download');
      const pct = job.progress != null ? `${Math.round(Number(job.progress))}%` : '';
      const detail = escapeHtml(job.status_detail || job.status || 'downloading');
      return `<div class="lm-components-download-row">
        <strong>${label}</strong>
        <span class="lm-setting-desc">${detail}${pct ? ` · ${pct}` : ''}</span>
      </div>`;
    }).join('');
  }

  function updateComponentsNavBadge(payload) {
    const badge = document.getElementById('componentsNavBadge');
    if (!badge) return;
    const count = Number(payload?.attention_count || 0);
    if (count > 0) {
      badge.textContent = String(count);
      badge.classList.remove('hidden');
    } else {
      badge.textContent = '';
      badge.classList.add('hidden');
    }
  }

  function renderComponentsAttentionBanner(payload) {
    const banner = document.getElementById('componentsAttentionBanner');
    if (!banner) return;
    const missing = Number(payload?.missing_count || 0);
    const updates = Number(payload?.update_count || 0);
    const parts = [];
    if (missing > 0) parts.push(`${missing} engine${missing === 1 ? '' : 's'} need installation`);
    if (updates > 0) parts.push(`${updates} update${updates === 1 ? '' : 's'} available`);
    if (!parts.length) {
      banner.classList.add('hidden');
      banner.textContent = '';
      return;
    }
    banner.classList.remove('hidden');
    banner.textContent = parts.join(' · ') + '. Install or update below.';
  }

  function stopComponentsPolling() {
    if (!componentsPollTimer) return;
    clearInterval(componentsPollTimer);
    componentsPollTimer = null;
  }

  function startComponentsPolling() {
    if (componentsPollTimer) return;
    componentsPollTimer = window.setInterval(() => {
      if (document.body.dataset.activeView !== 'settings') return;
      if (activePanelId() !== 'dl-components') return;
      void refreshComponentsPanel({ quiet: true });
    }, 4000);
  }

  async function refreshComponentsPanel({ quiet = false } = {}) {
    const listEl = document.getElementById('componentsHubList');
    if (!listEl) return;
    if (componentsRefreshInFlight) return componentsRefreshInFlight;
    if (!quiet) listEl.innerHTML = '<p class="lm-setting-desc">Loading components…</p>';
    const run = (async () => {
      try {
        const data = await api('/api/components', { timeoutMs: 12000 });
        renderComponentsList(data?.components);
        renderComponentsDownloads(data?.active_downloads);
        renderComponentsAttentionBanner(data);
        updateComponentsNavBadge(data);
        const installing = (data?.components || []).some((row) => row?.status === 'installing');
        if (installing) startComponentsPolling();
        else if (!Number(data?.active_download_count || 0)) stopComponentsPolling();
        else startComponentsPolling();
      } catch (err) {
        if (!quiet) {
          listEl.innerHTML = `<p class="lm-setting-desc lm-settings-load-err">${escapeHtml(err.message || 'Could not load components')}</p>`;
        }
      }
    })();
    componentsRefreshInFlight = run.finally(() => {
      componentsRefreshInFlight = null;
    });
    return componentsRefreshInFlight;
  }

  async function installComponent(runtimeId) {
    const rid = String(runtimeId || '').trim().toLowerCase();
    if (!rid) return;
    const btn = document.querySelector(`[data-action="component-install"][data-component-id="${rid}"]`);
    if (btn) btn.disabled = true;
    try {
      const body = {};
      if (rid === 'vllm') {
        const pick = document.querySelector(`[data-component-option="backend"][data-component-id="${rid}"]`);
        body.backend = pick?.value || 'auto';
      }
      if (rid === 'transformers') {
        const pick = document.querySelector(`[data-component-option="torch_variant"][data-component-id="${rid}"]`);
        body.torch_variant = pick?.value || 'auto';
      }
      await api(`/api/runtimes/${encodeURIComponent(rid)}/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        timeoutMs: 15000,
      });
      toast(`${rid} install started`);
      void refreshComponentsPanel();
    } catch (err) {
      toast(err.message || 'Could not start install', false);
      if (btn) btn.disabled = false;
    }
  }

  function openComponentConfirm({ title, message, sub = '', confirmLabel = 'Confirm', kicker = 'Remove module' }) {
    const modal = document.getElementById('deleteModelConfirmModal');
    if (!modal) return Promise.resolve(false);
    const titleEl = document.getElementById('deleteModelConfirmTitle');
    const messageEl = document.getElementById('deleteModelConfirmMessage');
    const subEl = document.getElementById('deleteModelConfirmSub');
    const confirmBtn = document.getElementById('deleteModelConfirm');
    const cancelBtn = document.getElementById('deleteModelCancel');
    const kickerEl = modal.querySelector('.df-update-kicker');
    if (titleEl) titleEl.textContent = title || 'Confirm';
    if (messageEl) messageEl.textContent = message || '';
    if (subEl) subEl.textContent = sub || '';
    if (confirmBtn) {
      confirmBtn.textContent = confirmLabel;
      confirmBtn.classList.remove('danger');
      confirmBtn.classList.add('primary');
    }
    if (kickerEl) kickerEl.textContent = kicker;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    return new Promise((resolve) => {
      const backdrop = modal.querySelector('.lm-modal-backdrop');
      const closeBtn = document.getElementById('deleteModelConfirmClose')
        || modal.querySelector('[data-action="close-modal"]');
      const cleanup = (result) => {
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
        if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
        if (confirmBtn) {
          confirmBtn.classList.add('danger');
          confirmBtn.classList.remove('primary');
          confirmBtn.textContent = 'Delete';
        }
        if (kickerEl) kickerEl.textContent = 'Delete model';
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
        if (e.target === backdrop) onCancel();
      };
      cancelBtn?.addEventListener('click', onCancel);
      confirmBtn?.addEventListener('click', onConfirm);
      closeBtn?.addEventListener('click', onClose);
      backdrop?.addEventListener('click', onBackdrop);
    });
  }

  async function uninstallComponent(runtimeId) {
    const rid = String(runtimeId || '').trim().toLowerCase();
    if (!rid) return;
    const label = rid === 'vllm' ? 'vLLM' : rid === 'transformers' ? 'Transformers' : rid;
    const accepted = await openComponentConfirm({
      title: `Uninstall ${label}?`,
      message: `This removes the downloaded ${label} engine from your PC. Models on disk are not deleted.`,
      sub: 'You can install again later from here or when loading a model.',
      confirmLabel: 'Uninstall',
      kicker: 'Remove module',
    });
    if (!accepted) return;
    try {
      await api(`/api/runtimes/${encodeURIComponent(rid)}/uninstall`, {
        method: 'POST',
        timeoutMs: 120000,
      });
      toast(`${label} removed`);
      void refreshComponentsPanel();
    } catch (err) {
      toast(err.message || 'Could not uninstall', false);
    }
  }

  function buildSettingsSearchIndex() {
    const items = [];
    document.querySelectorAll('.lm-settings-nav-item[data-settings-panel]').forEach((btn) => {
      const panelId = btn.dataset.settingsPanel;
      const label = btn.textContent.replace(/\s+/g, ' ').trim();
      items.push({
        panelId,
        sectionId: null,
        label,
        text: label,
        kind: 'page',
      });
    });
    document.querySelectorAll('.lm-settings-panel[data-settings-panel]').forEach((panel) => {
      const panelId = panel.dataset.settingsPanel;
      const pageTitle = panel.querySelector('.lm-settings-header h2')?.textContent?.trim() || panelId;
      panel.querySelectorAll('.lm-settings-section, .lm-setting-desc[data-settings-search]').forEach((sec, index) => {
        const strong = sec.querySelector('strong');
        const desc = sec.querySelector('.lm-setting-desc');
        const extra = sec.getAttribute('data-settings-search') || '';
        const sectionId = sec.id || `settings-sec-${panelId}-${index}`;
        if (!sec.id) sec.id = sectionId;
        const label = strong?.textContent?.trim() || pageTitle;
        const text = [pageTitle, label, desc?.textContent, extra, sec.textContent].filter(Boolean).join(' ');
        items.push({
          panelId,
          sectionId,
          label: `${pageTitle} — ${label}`,
          text,
          kind: 'section',
        });
      });
    });
    settingsSearchIndex = items;
  }

  function settingsSearchMatches(query, text) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return true;
    const hay = String(text || '').toLowerCase();
    return q.split(/\s+/).every((token) => hay.includes(token));
  }

  function renderSettingsSearchResults(query) {
    const resultsEl = document.getElementById('settingsSearchResults');
    if (!resultsEl) return;
    const q = String(query || '').trim();
    if (!q) {
      resultsEl.classList.add('hidden');
      resultsEl.innerHTML = '';
      document.querySelectorAll('.lm-settings-nav-item[data-settings-panel]').forEach((item) => {
        item.classList.remove('hidden');
      });
      document.querySelectorAll('.lm-settings-nav-section').forEach((sec) => {
        sec.classList.remove('hidden');
      });
      return;
    }
    const matches = settingsSearchIndex.filter((row) => settingsSearchMatches(q, row.text));
    const panelIds = new Set(matches.map((row) => row.panelId));
    document.querySelectorAll('.lm-settings-nav-item[data-settings-panel]').forEach((item) => {
      item.classList.toggle('hidden', !panelIds.has(item.dataset.settingsPanel));
    });
    document.querySelectorAll('.lm-settings-nav-section').forEach((sec) => {
      const nextBtn = sec.nextElementSibling;
      if (!nextBtn?.classList?.contains('lm-settings-nav-item')) {
        sec.classList.remove('hidden');
        return;
      }
      let anyVisible = false;
      let el = sec.nextElementSibling;
      while (el && !el.classList.contains('lm-settings-nav-section')) {
        if (el.classList.contains('lm-settings-nav-item') && !el.classList.contains('hidden')) anyVisible = true;
        el = el.nextElementSibling;
      }
      sec.classList.toggle('hidden', !anyVisible);
    });
    if (!matches.length) {
      resultsEl.innerHTML = '<div class="lm-settings-search-empty">No settings match your search.</div>';
      resultsEl.classList.remove('hidden');
      return;
    }
    const seen = new Set();
    const deduped = matches.filter((row) => {
      const key = `${row.panelId}:${row.sectionId || ''}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 12);
    resultsEl.innerHTML = deduped.map((row, index) => `
      <button type="button" class="lm-settings-search-hit" role="option" data-search-index="${index}" data-panel-id="${escapeHtml(row.panelId)}" data-section-id="${escapeHtml(row.sectionId || '')}">
        <span class="lm-settings-search-hit-label">${escapeHtml(row.label)}</span>
        <span class="lm-settings-search-hit-kind">${escapeHtml(row.kind === 'page' ? 'Page' : 'Section')}</span>
      </button>
    `).join('');
    resultsEl.classList.remove('hidden');
    resultsEl._hits = deduped;
  }

  function jumpToSettingsSearchHit(panelId, sectionId) {
    showPanel(panelId);
    const resultsEl = document.getElementById('settingsSearchResults');
    const input = document.getElementById('settingsSearchInput');
    if (resultsEl) {
      resultsEl.classList.add('hidden');
      resultsEl.innerHTML = '';
    }
    if (input) input.blur();
    window.requestAnimationFrame(() => {
      if (sectionId) {
        const target = document.getElementById(sectionId);
        if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          target.classList.add('lm-settings-search-flash');
          window.setTimeout(() => target.classList.remove('lm-settings-search-flash'), 1400);
        }
      }
    });
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
      else if (field === 'compute_type') row.compute_type = el.value;
      else if (field === 'language') row.language = el.value;
      else if (field === 'task') row.task = el.value;
      else if (field === 'beam_size') row.beam_size = Math.max(1, Math.min(16, Number(el.value) || 5));
      else if (field === 'vad_filter') row.vad_filter = el.checked;
      else if (field === 'cpu_threads') row.cpu_threads = Math.max(0, Number(el.value) || 0);
      else if (field === 'cfg_scale') row.cfg_scale = Math.max(0.5, Math.min(10, Number(el.value) || 1.5));
      else if (field === 'ddpm_steps') row.ddpm_steps = Math.max(1, Math.min(20, Number(el.value) || 5));
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
      compute_type: row.compute_type,
      language: row.language,
      task: row.task,
      beam_size: row.beam_size,
      vad_filter: row.vad_filter,
      temperature: row.temperature,
      cpu_threads: row.cpu_threads,
      num_workers: row.num_workers,
      cfg_scale: row.cfg_scale,
      ddpm_steps: row.ddpm_steps,
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
        applyHardwareSettingsFromServer(data);
        if (!downloadSettingsDraft) {
          downloadSettingsDraft = { ...(data.download_settings || { parallel_connections: 4 }) };
        }
        librariesDraft = [...(data.model_libraries || hardwareData?.model_libraries || [])];
        renderWorkspacePaths();
        const panel = activePanelId();
        if (HW_PANELS.has(panel)) renderHardwareForPanel(panel);
        if (panel === 'app-settings') renderWorkspacePaths();
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
    const targets = Array.isArray(result.reload_targets) ? result.reload_targets : [];
    if (!targets.length) {
      toast(result.reload_message || 'Compute settings saved');
      return;
    }
    await applyHardwareReloads(targets);
  }

  function closeHardwareApplyModal() {
    const modal = document.getElementById('hardwareApplyModal');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
  }

  function renderHardwareApplyList(items) {
    const list = document.getElementById('hardwareApplyList');
    if (!list) return;
    list.innerHTML = items.map((item) => {
      const models = (item.models || []).join(', ') || item.label;
      return `<li><strong>${escapeHtml(item.label)}</strong> — ${escapeHtml(models)} · ${escapeHtml(item.state || 'Waiting')}</li>`;
    }).join('');
  }

  async function applyHardwareReloads(targets) {
    const modal = document.getElementById('hardwareApplyModal');
    const messageEl = document.getElementById('hardwareApplyMessage');
    const closeBtn = document.getElementById('hardwareApplyClose');
    const items = targets.map((row) => ({ ...row, state: 'Waiting' }));
    if (messageEl) {
      messageEl.textContent = 'Loaded models keep the old GPU layout until they are started again. Reloading them now. You do not restart DFlash Console.';
    }
    if (closeBtn) closeBtn.hidden = true;
    renderHardwareApplyList(items);
    if (modal) {
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
    }
    let failed = 0;
    for (const item of items) {
      item.state = 'Reloading…';
      renderHardwareApplyList(items);
      try {
        await api(`/api/servers/${encodeURIComponent(item.server_id)}/reload`, {
          method: 'POST',
          timeoutMs: 0,
        });
        item.state = 'Done';
      } catch (err) {
        failed += 1;
        item.state = err.message || 'Failed';
      }
      renderHardwareApplyList(items);
    }
    if (messageEl) {
      messageEl.textContent = failed
        ? 'Some models could not be reloaded. Check Engines and try Load again.'
        : 'GPU layout is now active on the reloaded models.';
    }
    if (closeBtn) closeBtn.hidden = false;
    toast(failed ? 'GPU layout saved, but a reload failed' : 'GPU layout applied', !failed);
    window.DFlashServerLive?.refresh?.();
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
      void fetchHardware({ retries: 1 }).catch(() => {});
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

    gpuStrategyInputs().forEach((el) => {
      el.addEventListener('change', scheduleHardwareSave);
    });
    document.getElementById('hardwareApplyClose')?.addEventListener('click', closeHardwareApplyModal);
    document.getElementById('settingsLimitDedicatedVram')?.addEventListener('change', scheduleHardwareSave);
    document.getElementById('settingsOffloadKvGpu')?.addEventListener('change', scheduleHardwareSave);
    document.getElementById('settingsHardwareCopy')?.addEventListener('click', copyHardwareInfo);
    document.getElementById('settingsMcpCopy')?.addEventListener('click', copyMcpJson);
    document.getElementById('runtimeSettingsSave')?.addEventListener('click', () => void persistRuntimes());
    document.getElementById('componentsHubList')?.addEventListener('click', (e) => {
      const installBtn = e.target.closest('[data-action="component-install"]');
      if (installBtn) {
        void installComponent(installBtn.dataset.componentId);
        return;
      }
      const uninstallBtn = e.target.closest('[data-action="component-uninstall"]');
      if (uninstallBtn) void uninstallComponent(uninstallBtn.dataset.componentId);
    });
    document.querySelectorAll('[data-action="open-catalog-tab"]').forEach((btn) => {
      btn.addEventListener('click', () => window.DFlashShell?.setView?.('catalog'));
    });
    document.querySelectorAll('[data-action="open-downloads-tab"]').forEach((btn) => {
      btn.addEventListener('click', () => window.DFlashShell?.setView?.('downloads'));
    });

    const searchInput = document.getElementById('settingsSearchInput');
    const searchResults = document.getElementById('settingsSearchResults');
    if (searchInput) {
      searchInput.addEventListener('input', () => renderSettingsSearchResults(searchInput.value));
      searchInput.addEventListener('focus', () => {
        if (searchInput.value.trim()) renderSettingsSearchResults(searchInput.value);
      });
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          searchInput.value = '';
          renderSettingsSearchResults('');
        }
      });
    }
    if (searchResults) {
      searchResults.addEventListener('click', (e) => {
        const hit = e.target.closest('.lm-settings-search-hit');
        if (!hit) return;
        jumpToSettingsSearchHit(hit.dataset.panelId, hit.dataset.sectionId || '');
      });
    }
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.lm-settings-search-bar')) {
        if (searchResults && !searchInput?.value?.trim()) searchResults.classList.add('hidden');
      }
    });

    buildSettingsSearchIndex();
    window.addEventListener('focus', () => {
      if (document.body.dataset.activeView !== 'settings') return;
      if (hardwareDirty()) return;
      void fetchHardware({ retries: 1 }).catch(() => {});
    });
    document.getElementById('runtimeSettingsList')?.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action="stt-load"], [data-action="stt-unload"]');
      if (!btn) return;
      const action = btn.dataset.action === 'stt-unload' ? 'unload' : 'load';
      void runSttAction(Number(btn.dataset.rtIndex || 0), action);
    });
    document.getElementById('settingsAddLibrary')?.addEventListener('click', openLibraryBrowse);
    document.getElementById('settingsScanLibrary')?.addEventListener('click', openLibraryScan);
    document.getElementById('settingsDownloadLibrary')?.addEventListener('change', scheduleLibrariesSave);
    document.getElementById('settingsDownloadConnections')?.addEventListener('change', scheduleDownloadSettingsSave);
    document.getElementById('settingsDownloadBenchmarkBtn')?.addEventListener('click', () => void runDownloadBenchmark());
    document.getElementById('settingsExportConfig')?.addEventListener('click', () => void exportConfigFile());
    document.getElementById('settingsImportConfig')?.addEventListener('click', () => void importConfigFile());
    document.getElementById('settingsExportPresets')?.addEventListener('click', () => void exportPresetFiles());
    document.getElementById('settingsImportPresets')?.addEventListener('click', () => void importPresetFiles());

    ['serverSettingsPort', 'serverSettingsHost', 'serverSettingsContext', 'serverSettingsContextMax', 'serverSettingsIdle',
      'serverSettingsProfile', 'serverSettingsGpu'].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('change', scheduleGatewaySave);
      el.addEventListener('input', () => {
        if (id === 'serverSettingsPort' || id === 'serverSettingsHost') updateGatewayApiUrl();
      });
    });

    ['settingsGatewayPort', 'settingsGatewayServer'].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('change', () => {
        if (id === 'settingsGatewayPort') updateGatewayUrl();
        void saveGatewayConfig();
      });
      if (id === 'settingsGatewayPort') {
        el.addEventListener('input', updateGatewayUrl);
      }
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
    refreshComponentsPanel,
    addLibrariesFromScan,
    addLibraryFromBrowse,
    importLibraryAndAdd,
    importLibrariesAndAdd,
  };
})();
