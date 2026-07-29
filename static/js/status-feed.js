/** Global top-bar status feed — background activity across all tabs */
(function () {
  const { api } = window.StudioApi;

  let transient = null;
  let transientTimer = null;
  let serversSnapshot = [];

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

  function buildFromServers(servers) {
    const loading = [];
    const loaded = [];
    const idle = [];
    for (const server of servers) {
      const label = server.label || server.id || 'Model';
      if (server.status === 'booting') {
        const pct = server.load_progress != null ? ` · ${Math.round(server.load_progress)}%` : '';
        loading.push(`Loading ${label}${pct}`);
      } else if (server.status === 'loaded') {
        loaded.push(`${label} ready on :${server.port || '—'}`);
      } else if (server.running) {
        idle.push(`${label} listening :${server.port || '—'}`);
      }
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
        secondary: loaded.slice(0, 2).join(' · '),
      };
    }
    if (idle.length === 1) {
      return { primary: idle[0], secondary: 'No model loaded yet' };
    }
    if (idle.length > 1) {
      return { primary: `${idle.length} servers running`, secondary: 'No models loaded' };
    }
    return { primary: 'Ready', secondary: '' };
  }

  function refreshDisplay() {
    const built = buildFromServers(serversSnapshot);
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
    try {
      const data = await api('/api/servers');
      serversSnapshot = data.servers || [];
      refreshDisplay();
    } catch {
      if (!transient) render('Ready', '');
    }
  }

  function startPolling() {
    void poll();
    window.setInterval(poll, 2500);
  }

  document.addEventListener('DOMContentLoaded', startPolling);

  window.DFlashStatusFeed = {
    setTransient,
    note,
    refresh: poll,
    getServers: () => serversSnapshot,
  };
})();
