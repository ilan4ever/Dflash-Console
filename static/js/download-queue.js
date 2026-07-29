/** Global Hugging Face download queue — visible from the main sysbar on every tab. */
(function () {
  const { api } = window.ConsoleApi;

  const jobs = new Map();
  const labels = new Map();
  const listeners = new Set();
  let pollTimer = null;
  let panelOpen = false;

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = bytes;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit += 1;
    }
    return `${size >= 100 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
  }

  function progressLabel(job) {
    const pct = job?.progress;
    if (pct != null && Number.isFinite(Number(pct))) return `${Math.round(Number(pct))}%`;
    if (job?.bytes_read) return formatBytes(job.bytes_read);
    return 'Starting…';
  }

  function progressWidth(job) {
    const pct = Number(job?.progress);
    if (Number.isFinite(pct) && pct >= 0) return Math.max(2, Math.min(100, pct));
    return null;
  }

  function badgeEl() {
    return document.getElementById('dfDownloadsBadge');
  }

  function panelEl() {
    return document.getElementById('dfDownloadsPanel');
  }

  function listEl() {
    return document.getElementById('dfDownloadsList');
  }

  function subEl() {
    return document.getElementById('dfDownloadsPanelSub');
  }

  function toggleEl() {
    return document.getElementById('dfDownloadsToggle');
  }

  function trayEl() {
    return document.getElementById('dfDownloadsTray');
  }

  function sortedJobs() {
    return [...jobs.values()].sort((a, b) => Number(b.started_at || 0) - Number(a.started_at || 0));
  }

  function activeJobs() {
    return sortedJobs().filter((job) => job.status === 'downloading');
  }

  function emit() {
    const snapshot = sortedJobs();
    listeners.forEach((fn) => {
      try {
        fn(snapshot);
      } catch {
        /* ignore */
      }
    });
    renderPanel(snapshot);
    renderBadge(activeJobs().length);
    renderStatusFeed(activeJobs());
  }

  function renderBadge(count) {
    const badge = badgeEl();
    const toggle = toggleEl();
    const tray = trayEl();
    if (!badge || !toggle || !tray) return;
    badge.textContent = String(count);
    badge.classList.toggle('hidden', count <= 0);
    tray.classList.toggle('has-active', count > 0);
    toggle.classList.toggle('is-active', count > 0);
  }

  function renderStatusFeed(active) {
    if (!active.length) return;
    const primary = active.length === 1
      ? `Downloading ${labels.get(active[0].id) || active[0].filename || 'model'} · ${progressLabel(active[0])}`
      : `Downloading ${active.length} models`;
    const secondary = active.length === 1
      ? active[0].repo_id || ''
      : active.slice(0, 2).map((job) => labels.get(job.id) || job.filename).join(' · ');
    window.DFlashStatusFeed?.setTransient(primary, { secondary, ttlMs: 15000 });
  }

  function renderPanel(snapshot) {
    const list = listEl();
    const sub = subEl();
    if (!list || !sub) return;

    const active = snapshot.filter((job) => job.status === 'downloading');
    const recent = snapshot.filter((job) => job.status !== 'downloading').slice(0, 6);
    const rows = [...active, ...recent];

    sub.textContent = active.length
      ? `${active.length} active download${active.length === 1 ? '' : 's'}`
      : (recent.length ? 'Recent downloads' : 'No active downloads');

    if (!rows.length) {
      list.innerHTML = '<p class="df-downloads-empty">No downloads yet.</p>';
      return;
    }

    list.innerHTML = rows.map((job) => {
      const title = labels.get(job.id) || job.filename || job.repo_id || 'Model';
      const width = progressWidth(job);
      const indeterminate = job.status === 'downloading' && width == null;
      const fillStyle = width != null ? ` style="width:${width}%"` : '';
      const fillClass = indeterminate ? ' is-indeterminate' : '';
      const statusText = job.status === 'downloading'
        ? progressLabel(job)
        : (job.status === 'done' ? 'Complete' : (job.error || 'Failed'));
      const detail = job.status === 'downloading'
        ? `${escapeHtml(job.filename || '')}${job.bytes_total ? ` · ${formatBytes(job.bytes_read)} / ${formatBytes(job.bytes_total)}` : ''}`
        : escapeHtml(job.path || job.repo_id || '');
      const bar = job.status === 'downloading'
        ? `<div class="df-downloads-item-bar"><div class="df-downloads-item-fill${fillClass}"${fillStyle}></div></div>`
        : '';
      return `
        <div class="df-downloads-item${job.status === 'error' ? ' is-error' : ''}${job.status === 'done' ? ' is-done' : ''}">
          <div class="df-downloads-item-head">
            <span class="df-downloads-item-title">${escapeHtml(title)}</span>
            <span class="df-downloads-item-status">${escapeHtml(statusText)}</span>
          </div>
          <div class="df-downloads-item-meta">${detail}</div>
          ${bar}
        </div>`;
    }).join('');
  }

  function ensurePolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      void refresh();
    }, 1000);
  }

  function stopPollingIfIdle() {
    if (activeJobs().length) return;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function refresh() {
    try {
      const data = await api('/api/hf/downloads');
      const incoming = data.jobs || [];
      incoming.forEach((job) => {
        jobs.set(job.id, job);
      });
      emit();
      if (activeJobs().length) ensurePolling();
      else stopPollingIfIdle();
    } catch {
      stopPollingIfIdle();
    }
  }

  function track(meta) {
    const jobId = String(meta?.jobId || meta?.job_id || '').trim();
    if (!jobId) return;
    if (meta?.label) labels.set(jobId, String(meta.label));
    jobs.set(jobId, {
      id: jobId,
      repo_id: meta?.repoId || meta?.repo_id || '',
      filename: meta?.filename || '',
      status: 'downloading',
      progress: 0,
      started_at: Date.now() / 1000,
      path: meta?.path || '',
    });
    ensurePolling();
    void refresh();
    openPanel();
    emit();
  }

  function getActiveJob(repoId, filename) {
    const repo = String(repoId || '').trim();
    const file = String(filename || '').trim();
    return sortedJobs().find((job) =>
      job.status === 'downloading'
      && String(job.repo_id || '') === repo
      && String(job.filename || '') === file,
    ) || null;
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function openPanel() {
    panelOpen = true;
    panelEl()?.classList.remove('hidden');
    toggleEl()?.setAttribute('aria-expanded', 'true');
  }

  function closePanel() {
    panelOpen = false;
    panelEl()?.classList.add('hidden');
    toggleEl()?.setAttribute('aria-expanded', 'false');
  }

  function togglePanel() {
    if (panelOpen) closePanel();
    else openPanel();
  }

  function bind() {
    toggleEl()?.addEventListener('click', (event) => {
      event.stopPropagation();
      togglePanel();
    });
    document.addEventListener('click', (event) => {
      if (!panelOpen) return;
      if (trayEl()?.contains(event.target)) return;
      closePanel();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closePanel();
    });
    void refresh();
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashDownloadQueue = {
    track,
    refresh,
    subscribe,
    getActiveJob,
    getActiveJobs: activeJobs,
    getJobs: sortedJobs,
    getJobLabel(job) {
      return labels.get(job?.id) || job?.filename || job?.repo_id || 'Model';
    },
    openPanel,
    closePanel,
    progressLabel,
    progressWidth,
    formatBytes,
  };
})();
