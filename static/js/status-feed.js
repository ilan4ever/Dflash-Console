/** Global top-bar status feed — background activity across all tabs */
(function () {
  const { api } = window.ConsoleApi;

  let transient = null;
  let transientTimer = null;
  let serversSnapshot = [];
  let externalSnapshot = [];
  let pollInFlight = false;
  let latestSnapshotRevision = 0;
  /** serverId -> { label } — set by Engines tab while a user-initiated load is in flight */
  let pendingLoadsSnapshot = {};

  function normalizeLoadProgress(raw) {
    if (raw == null) return { pct: null, detail: '', phase: '' };
    if (typeof raw === 'object' && !Array.isArray(raw)) {
      const pctRaw = raw.expert_pct ?? raw.pct ?? raw.progress ?? null;
      const pct = pctRaw != null && Number.isFinite(Number(pctRaw)) ? Number(pctRaw) : null;
      return {
        pct,
        detail: String(raw.detail || '').trim(),
        phase: String(raw.phase || '').trim(),
        eta_seconds: Number.isFinite(Number(raw.eta_seconds)) ? Number(raw.eta_seconds) : null,
        elapsed_seconds: Number.isFinite(Number(raw.elapsed_seconds)) ? Number(raw.elapsed_seconds) : null,
        expert_present: Number.isFinite(Number(raw.expert_present)) ? Number(raw.expert_present) : null,
        expert_total: Number.isFinite(Number(raw.expert_total)) ? Number(raw.expert_total) : null,
      };
    }
    const num = Number(raw);
    return {
      pct: Number.isFinite(num) && num > 0 ? num : null,
      detail: '',
      phase: '',
    };
  }

  function loadProgressSuffix(raw) {
    const progress = normalizeLoadProgress(raw);
    const parts = [];
    if (progress.pct != null) parts.push(`${Math.round(progress.pct)}%`);
    if (progress.eta_seconds != null && progress.eta_seconds >= 0) {
      const total = Math.round(progress.eta_seconds);
      parts.push(total < 60 ? `~${total}s left` : `~${Math.ceil(total / 60)}m left`);
    }
    return parts.length ? ` · ${parts.join(' · ')}` : '';
  }

  function serverIsWarming(server) {
    return !!(server?.warming || server?.booting || server?.status === 'booting');
  }

  function primaryEl() {
    return document.getElementById('statusFeedPrimary');
  }

  function secondaryEl() {
    return document.getElementById('statusFeedSecondary');
  }

  function render(primary, secondary) {
    const p = primaryEl();
    const s = secondaryEl();
    if (p) p.textContent = primary || 'Ready';
    if (s) {
      s.textContent = secondary || '';
      s.classList.toggle('hidden', !secondary);
    }
    const feed = document.getElementById('statusFeed');
    if (feed) {
      feed.classList.toggle('active', primary !== 'Ready' || !!secondary);
      feed.classList.toggle('loading', /loading|starting|booting/i.test(primary || ''));
    }
  }

  function loadingModelLabel(server) {
    const pending = pendingLoadsSnapshot[server.id];
    if (pending?.label) return pending.label;
    const cards = Array.isArray(server.visible_cards) ? server.visible_cards : [];
    const loadingCard = cards.find((row) => row.card_state === 'loading');
    if (loadingCard?.title) return loadingCard.title;
    if (server.model_id && serverIsWarming(server)) {
      return String(server.model_id).replace(/-/g, ' ');
    }
    if (server.active_model_id) return String(server.active_model_id).replace(/-/g, ' ');
    const modelPath = String(server.model_path || '').replace(/\\/g, '/');
    if (modelPath && serverIsWarming(server)) {
      const base = modelPath.split('/').pop();
      if (base) return base;
    }
    return server.label || server.id || 'Model';
  }

  function externalModelLabel(row) {
    const path = String(row?.model_path || row?.path || '').replace(/\\/g, '/');
    const basename = path.split('/').pop();
    return row?.title || row?.model_name || basename || row?.app_label || 'External model';
  }

  function loadedModelLabels(server) {
    const cards = Array.isArray(server?.visible_cards)
      ? server.visible_cards.filter((row) => row?.card_state !== 'loading')
      : [];
    if (cards.length) {
      return cards.map((row) => row.title || row.display_name_full || row.label || row.id || 'Model');
    }
    const loadedIds = (Array.isArray(server?.loaded_models) ? server.loaded_models : [])
      .map((modelId) => String(modelId || '').replace(/-/g, ' '))
      .filter(Boolean);
    return loadedIds.length
      ? loadedIds
      : [server?.active_model_id || server?.label || server?.id || 'Model'];
  }

  function buildFromServers(servers, externalRows = []) {
    const loading = [];
    const loaded = [];
    const idle = [];
    const pendingIds = new Set(Object.keys(pendingLoadsSnapshot));

    for (const [serverId, meta] of Object.entries(pendingLoadsSnapshot)) {
      const server = servers.find((row) => row.id === serverId);
      const label = meta?.label || server?.label || serverId;
      const pct = loadProgressSuffix(server?.load_progress);
      loading.push(`Loading ${label}${pct}`);
    }

    for (const server of servers) {
      if (pendingIds.has(server.id)) continue;
      const label = loadingModelLabel(server);
      if (serverIsWarming(server)) {
        const verb = server.warming || server.runtime_id === 'freetoken' ? 'Warming' : 'Loading';
        const pct = loadProgressSuffix(server.load_progress);
        const detail = normalizeLoadProgress(server.load_progress).detail;
        loading.push(detail && !pct ? `${verb} ${label} · ${detail}` : `${verb} ${label}${pct}`);
      } else if (server.status === 'loaded') {
        loadedModelLabels(server).forEach((readyLabel) => {
          loaded.push(`${readyLabel} ready on :${server.port || '—'}`);
        });
      } else if (server.running) {
        idle.push(`${server.label || server.id || 'Model'} listening :${server.port || '—'}`);
      }
    }
    for (const row of Array.isArray(externalRows) ? externalRows : []) {
      const port = row?.listen_port || row?.port || '—';
      loaded.push(`${externalModelLabel(row)} ready${port !== '—' ? ` on :${port}` : ''}`);
    }
    if (loading.length) {
      return {
        primary: loading[0],
        secondary: loading.length > 1
          ? `Also loading ${loading.length - 1} more`
          : (loaded[0] || ''),
      };
    }
    if (loaded.length === 1) {
      return { primary: loaded[0], secondary: 'Model loaded — ready for inference' };
    }
    if (loaded.length > 1) {
      return {
        primary: `${loaded.length} models loaded`,
        secondary: `${loaded.slice(0, 3).join(' · ')}${loaded.length > 3 ? ` · +${loaded.length - 3} more` : ''}`,
      };
    }
    if (idle.length === 1) {
      return { primary: `${idle[0].replace(/ listening :\d+$/, '')} ready to load`, secondary: 'No model loaded yet' };
    }
    if (idle.length > 1) {
      return { primary: `${idle.length} DFlash engines ready to load`, secondary: 'No models loaded' };
    }
    return { primary: 'Ready', secondary: '' };
  }

  function refreshDisplay() {
    const built = buildFromServers(serversSnapshot, externalSnapshot);
    const hasLiveActivity = built.primary !== 'Ready' || !!built.secondary;
    if (hasLiveActivity) {
      if (transientTimer) {
        clearTimeout(transientTimer);
        transientTimer = null;
      }
      transient = null;
      render(built.primary, built.secondary);
      return;
    }
    if (transient) {
      render(transient.primary, transient.secondary || '');
      return;
    }
    render(built.primary, built.secondary);
  }

  function setTransient(primary, { secondary = '', ttlMs = 8000 } = {}) {
    transient = { primary, secondary };
    refreshDisplay();
    if (transientTimer) clearTimeout(transientTimer);
    transientTimer = window.setTimeout(() => {
      transient = null;
      transientTimer = null;
      refreshDisplay();
    }, ttlMs);
  }

  function note(message, secondary = '') {
    setTransient(message, { secondary, ttlMs: 5000 });
  }

  async function poll() {
    if (pollInFlight) return;
    pollInFlight = true;
    try {
      const data = await api('/api/servers?include_external=1');
      const revision = Number(data?.snapshot_revision || 0);
      if (revision > 0 && latestSnapshotRevision > 0 && revision < latestSnapshotRevision) return;
      if (revision > 0) latestSnapshotRevision = revision;
      serversSnapshot = data.servers || [];
      if (Array.isArray(data.external_gpu_loads)) {
        externalSnapshot = data.external_gpu_loads;
      }
      refreshDisplay();
    } catch {
      // Keep the last successful engine/model state during a slow poll.
    } finally {
      pollInFlight = false;
    }
  }

  function startPolling() {
    void poll();
    window.setInterval(poll, 2500);
  }

  document.addEventListener('DOMContentLoaded', startPolling);

  function setPendingLoads(map) {
    pendingLoadsSnapshot = map && typeof map === 'object' ? { ...map } : {};
    refreshDisplay();
  }

  window.DFlashLoadProgress = {
    normalize: normalizeLoadProgress,
    suffix: loadProgressSuffix,
    isWarming: serverIsWarming,
  };

  window.DFlashStatusFeed = {
    setTransient,
    note,
    refresh: poll,
    getServers: () => serversSnapshot,
    setPendingLoads,
  };
})();
