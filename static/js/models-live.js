/** Models tab — local catalog + inspector binding */
(function () {
  const { api, toast } = window.ConsoleApi;

  let models = [];
  let meta = {};
  let selectedKey = localStorage.getItem('dflashConsole.selectedModelKey') || '';
  let loadedServerIds = new Set();
  let bootingServers = {};
  let contextModel = null;

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

  function capabilityTags(caps, { loadable = false, port = 0 } = {}) {
    const list = Array.isArray(caps) ? caps : [];
    const tags = [];
    if (loadable) {
      tags.push('<span class="lm-tag green">loadable</span>');
      tags.push(`<span class="lm-tag blue">port ${port || '—'}</span>`);
    }
    if (list.includes('tools')) tags.push('<span class="lm-tag green">tools</span>');
    if (list.includes('ar')) tags.push('<span class="lm-tag blue">AR</span>');
    if (list.includes('dflash')) tags.push('<span class="lm-tag green">dflash</span>');
    list.forEach((cap) => {
      if (cap === 'instruct' || cap === 'tools' || cap === 'ar' || cap === 'dflash') return;
      tags.push(`<span class="lm-tag blue">${escapeHtml(cap)}</span>`);
    });
    return tags.join('');
  }

  function capTags(model) {
    return capabilityTags(model.capabilities, { loadable: model.loadable, port: model.port });
  }

  function isDflashModel(model) {
    const caps = Array.isArray(model.capabilities) ? model.capabilities : [];
    return caps.includes('dflash') || (model.loadable && !!model.draft_path);
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
    loadedServerIds = new Set();
    bootingServers = {};
    for (const server of serversData.servers || []) {
      serverMap[server.id] = server;
      if (server.status === 'loaded') loadedServerIds.add(server.id);
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
          runtime_loaded: server.status === 'loaded',
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
      }
      return merged;
    });
  }

  function modelIdentifier(model) {
    return model.server_id || model.path || model.id || '';
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
    return `
      <tr class="lm-model-row downloading-model" data-download-job-id="${escapeHtml(job.id)}">
        <td class="lm-col-model">
          <div class="lm-model-title-line">
            <span class="lm-tag green">downloading</span>
            <span class="lm-llm-name">${escapeHtml(downloadJobTitle(job))}</span>
            <span class="lm-model-download-pct">${escapeHtml(pctLabel)}</span>
          </div>
          <div class="lm-model-download-bar" aria-hidden="true">
            <div class="lm-model-download-fill${fillClass}"${fillStyle}></div>
          </div>
          <div class="lm-model-meta-line lm-model-download-meta">${escapeHtml(repo)} · ${escapeHtml(filename)}</div>
        </td>
        <td>—</td>
        <td>—</td>
        <td>${escapeHtml(repo.split('/')[0] || 'HF')}</td>
        <td>${escapeHtml(bytes)}</td>
        <td>Now</td>
        <td><span class="lm-tag dim">in progress</span></td>
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

  let typeFilter = localStorage.getItem('dflashConsole.modelsTypeFilter') || 'dflash';

  function renderTable(filterText) {
    const body = document.getElementById('modelsTableBody');
    if (!body) return;
    const needle = String(filterText || '').trim().toLowerCase();
    const pinned = loadPinnedSet();
    const activeDownloads = filterDownloadJobs(getActiveDownloadJobs(), needle);
    const catalogRows = models.filter((model) => {
      if (typeFilter === 'dflash' && !isDflashModel(model)) return false;
      if (typeFilter === 'downloading') return false;
      if (!needle) return true;
      const hay = [
        model.label, model.id, model.path, model.publisher, model.arch, model.quant,
        model.draft_label, model.draft_filename, model.draft_path,
      ].join(' ').toLowerCase();
      return hay.includes(needle);
    }).sort((a, b) => {
      const aPin = pinned.has(modelKey(a)) ? 0 : 1;
      const bPin = pinned.has(modelKey(b)) ? 0 : 1;
      if (aPin !== bPin) return aPin - bPin;
      const aScore = (a.loadable ? 0 : 1) + (isDflashModel(a) ? 0 : 0.5);
      const bScore = (b.loadable ? 0 : 1) + (isDflashModel(b) ? 0 : 0.5);
      if (aScore !== bScore) return aScore - bScore;
      return String(a.label || '').localeCompare(String(b.label || ''));
    });

    if (typeFilter === 'downloading') {
      body.innerHTML = activeDownloads.length
        ? activeDownloads.map((job) => renderDownloadingRow(job)).join('')
        : '<tr><td colspan="7" class="lm-models-empty">No models are downloading right now. Start a download from Model catalog.</td></tr>';
      return;
    }

    body.innerHTML = [
      ...activeDownloads.map((job) => renderDownloadingRow(job)),
      ...catalogRows.map((model) => {
      const key = modelKey(model);
      const selected = key === selectedKey ? ' selected' : '';
      const booting = model.server_id && bootingServers[model.server_id];
      const running = model.server_id && loadedServerIds.has(model.server_id) ? ' running-on-server' : '';
      const loading = booting ? ' loading-on-server' : '';
      const pinnedClass = pinned.has(key) ? ' pinned' : '';
      const size = model.size_gb != null ? `${model.size_gb} GB` : '—';
      const pinMark = pinned.has(key) ? '<span class="lm-model-pin" title="Pinned">📌</span>' : '';
      const quantLabel = model.quant && model.quant !== '—' ? `<span class="lm-model-quant-inline">${escapeHtml(model.quant)}</span>` : '';
      const loadBtn = model.loadable
        ? '<button class="lm-btn ghost tiny" type="button" data-action="load-model">Run</button>'
        : '<span class="lm-tag dim">browse</span>';
      return `
        <tr class="lm-model-row${selected}${running}${loading}${pinnedClass}" data-model-key="${escapeHtml(key)}" data-server-id="${escapeHtml(model.server_id || '')}">
          <td class="lm-col-model">
            <div class="lm-model-title-line">${pinMark}<span class="lm-llm-name">${escapeHtml(model.label || model.id || '—')}</span>${quantLabel}</div>
            <div class="lm-model-meta-line">${capTags(model)}${draftHint(model)}</div>
          </td>
          <td>${escapeHtml(model.arch || '—')}</td>
          <td>${escapeHtml(model.params || '—')}</td>
          <td>${escapeHtml(model.publisher || '—')}</td>
          <td>${escapeHtml(size)}</td>
          <td>${escapeHtml(model.modified || '—')}</td>
          <td>${loadBtn}</td>
        </tr>`;
    }),
    ].join('');

    body.querySelectorAll('.lm-model-row:not(.downloading-model)').forEach((row) => {
      row.addEventListener('click', (event) => {
        if (event.target.closest('[data-action="load-model"]')) return;
        void selectModel(row.dataset.modelKey);
      });
      row.addEventListener('dblclick', () => {
        const model = models.find((entry) => modelKey(entry) === row.dataset.modelKey);
        if (model?.loadable) void loadModel(model);
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
  }

  function hideContextMenu() {
    const menu = document.getElementById('modelsContextMenu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML = '';
    contextModel = null;
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

    menu.innerHTML = `
      <button type="button" data-cmd="pin">${isPinned ? 'Unpin' : 'Pin'}</button>
      <button type="button" data-cmd="copy-id">Copy identifier</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <button type="button" data-cmd="huggingface"${hfUrl ? '' : ' disabled'}>Open Hugging Face</button>
      <hr>
      <button type="button" data-cmd="load"${model.loadable ? '' : ' disabled'}>Load to Server</button>
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
  }

  async function runContextCommand(cmd, model) {
    const key = modelKey(model);
    if (cmd === 'pin') {
      const pinned = loadPinnedSet();
      if (pinned.has(key)) pinned.delete(key);
      else pinned.add(key);
      savePinnedSet(pinned);
      renderTable(document.getElementById('modelsFilterInput')?.value || '');
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
    if (cmd === 'huggingface') {
      const url = huggingFaceUrl(model);
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
      else toast('No Hugging Face URL for this model', false);
      return;
    }
    if (cmd === 'load') {
      if (model.loadable) await loadModel(model);
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
          ? models.filter(isDflashModel).length
          : models.length;
        const filterNote = typeFilter === 'dflash' ? ` · showing ${shown} DFlash` : '';
        const downloadNote = activeCount ? ` · ${activeCount} downloading` : '';
        stats.textContent = `${meta.total_count || models.length} models (${meta.loadable_count || 0} engine profiles), ${meta.total_size_gb || 0} GB total${filterNote}${downloadNote}`;
      }
    }
    if (path) path.textContent = meta.models_dir || '—';
    if (hint) {
      hint.textContent = typeFilter === 'downloading'
        ? 'Active Hugging Face downloads from Model catalog appear here with live progress.'
        : 'Green rows are running on an engine. Downloading models appear at the top with a progress bar.';
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
    renderTable(document.getElementById('modelsFilterInput')?.value || '');
    if (applyInspector && window.DFlashServerLive?.applyModelSelection) {
      await window.DFlashServerLive.applyModelSelection(model);
    }
  }

  async function loadModel(model) {
    if (!window.DFlashServerLive?.loadSelectedModel) {
      toast('Engine panel is not ready yet.', false);
      return;
    }
    if (!model?.loadable && !model?.path) {
      toast('This file is not available to load.', false);
      return;
    }
    window.DFlashStatusFeed?.setTransient(`Loading ${model.label || model.id}…`, {
      secondary: 'Reading weights into GPU',
      ttlMs: 120000,
    });
    localStorage.setItem('dflashConsole.activeTab', 'server');
    document.body.dataset.activeView = 'server';
    document.querySelectorAll('.lm-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.tab === 'server');
    });
    document.querySelectorAll('.lm-view').forEach((view) => {
      view.classList.toggle('active', view.dataset.view === 'server');
    });
    await window.DFlashServerLive.loadSelectedModel(model);
  }

  async function refresh({ rebindInspector = false } = {}) {
    const filter = document.getElementById('modelsFilterInput')?.value || '';
    const [data, serversData] = await Promise.all([
      api('/api/models'),
      api('/api/servers').catch(() => ({ servers: [] })),
    ]);
    models = mergeModelsWithState(data.models || [], serversData, loadBrowsePrefs());
    renderFooter(data);
    renderTable(filter);
    if (!selectedKey || !models.some((m) => modelKey(m) === selectedKey)) {
      const firstConfigured = models.find((m) => m.loadable);
      if (firstConfigured) await selectModel(modelKey(firstConfigured), { applyInspector: true });
      else if (models[0]) await selectModel(modelKey(models[0]), { applyInspector: true });
    } else if (rebindInspector) {
      await selectModel(selectedKey, { applyInspector: true });
    }
  }

  function setTypeFilter(next) {
    typeFilter = ['all', 'dflash', 'downloading'].includes(next) ? next : 'all';
    localStorage.setItem('dflashConsole.modelsTypeFilter', typeFilter);
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.modelsFilter === typeFilter);
    });
    renderTable(document.getElementById('modelsFilterInput')?.value || '');
    renderFooter(meta);
  }

  function onDownloadQueueUpdate() {
    if (document.body.dataset.activeView !== 'models') return;
    renderTable(document.getElementById('modelsFilterInput')?.value || '');
    renderFooter(meta);
    if (typeFilter === 'downloading' && !getActiveDownloadJobs().length) {
      /* keep filter selected; empty state shown */
    }
  }

  function bind() {
    document.getElementById('modelsFilterInput')?.addEventListener('input', (e) => {
      renderTable(e.target.value);
    });
    document.querySelectorAll('[data-models-filter]').forEach((btn) => {
      btn.addEventListener('click', () => setTypeFilter(btn.dataset.modelsFilter));
    });
    setTypeFilter(typeFilter);
    window.DFlashDownloadQueue?.subscribe?.(onDownloadQueueUpdate);

    document.addEventListener('click', hideContextMenu);
    document.addEventListener('scroll', hideContextMenu, true);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideContextMenu();
    });
  }

  let pollTimer = null;

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      if (document.body.dataset.activeView === 'models') {
        void refresh().catch(() => {});
      }
    }, 2500);
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    void refresh({ rebindInspector: true }).then(startPolling).catch((err) => toast(err.message, false));
  });

  window.DFlashModelsLive = { refresh, selectModel, loadModel };
})();
