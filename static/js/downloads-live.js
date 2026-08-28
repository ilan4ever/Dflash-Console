/** Dedicated Downloads page — current transfers and last finished jobs. */
(function () {
  const { api, toast } = window.ConsoleApi;

  const RANGE_KEY = 'dflashConsole.downloadsRange';
  let pane = 'active';
  let bound = false;
  let range = localStorage.getItem(RANGE_KEY) || 'all';

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function queue() {
    return window.DFlashDownloadQueue;
  }

  function allJobs() {
    return queue()?.getJobs?.() || [];
  }

  function activeJobs() {
    return allJobs().filter((job) => job.status === 'downloading');
  }

  function historyJobs() {
    const cutoff = rangeCutoff();
    return allJobs()
      .filter((job) => job.status !== 'downloading')
      .filter((job) => {
        if (cutoff == null) return true;
        const when = Number(job.finished_at || job.started_at || 0);
        return when >= cutoff;
      })
      .sort((a, b) => Number(b.finished_at || b.started_at || 0) - Number(a.finished_at || a.started_at || 0));
  }

  function rangeCutoff() {
    if (range === 'all') return null;
    const days = Number(range);
    if (!Number.isFinite(days) || days <= 0) return null;
    return Date.now() / 1000 - days * 86400;
  }

  function rangeLabel() {
    if (range === '1') return 'in the last 24 hours';
    if (range === '7') return 'in the last 7 days';
    if (range === '30') return 'in the last 30 days';
    if (range === '90') return 'in the last 90 days';
    if (range === '365') return 'in the last 12 months';
    return 'all time';
  }

  function formatWhen(ts) {
    const value = Number(ts);
    if (!Number.isFinite(value) || value <= 0) return '';
    try {
      return new Date(value * 1000).toLocaleString();
    } catch {
      return '';
    }
  }

  function jobTitle(job) {
    return queue()?.getJobLabel?.(job) || job.filename || job.repo_id || 'Model';
  }

  function setPane(next) {
    pane = next === 'history' ? 'history' : 'active';
    document.querySelectorAll('[data-downloads-pane]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.downloadsPane === pane);
    });
    render();
  }

  function render() {
    const list = document.getElementById('dfDownloadsPageList');
    const hint = document.getElementById('dfDownloadsPageHint');
    const clearBtn = document.getElementById('dfDownloadsClearAll');
    const rangeWrap = document.getElementById('dfDownloadsRangeWrap');
    const rangePick = document.getElementById('dfDownloadsRange');
    if (!list || !hint) return;

    const active = activeJobs();
    const history = historyJobs();
    const rows = pane === 'history' ? history : active;
    clearBtn?.classList.toggle('hidden', pane !== 'history' || !history.length);
    rangeWrap?.classList.toggle('hidden', pane !== 'history');
    if (rangePick && rangePick.value !== range) rangePick.value = range;

    if (pane === 'active') {
      hint.textContent = active.length
        ? `${active.length} downloading now`
        : 'Nothing is downloading right now. Start a model from Model catalog.';
    } else {
      hint.textContent = history.length
        ? `${history.length} download${history.length === 1 ? '' : 's'} ${rangeLabel()}`
        : `No downloads ${rangeLabel()}. Try a wider range.`;
    }

    if (!rows.length) {
      list.innerHTML = pane === 'active'
        ? '<p class="df-downloads-page-empty">No models are downloading at the moment.</p>'
        : `<p class="df-downloads-page-empty">No downloads ${rangeLabel()}.</p>`;
      return;
    }

    list.innerHTML = rows.map((job) => {
      const remove = job.status === 'downloading'
        ? ''
        : `<button type="button" class="lm-icon-btn tiny df-downloads-remove" data-clear-job="${escapeHtml(job.id)}" title="Remove from last downloads" aria-label="Remove from last downloads">×</button>`;
      return queue()?.renderDownloadCardHtml?.(job, { variant: 'page', removeButtonHtml: remove }) || '';
    }).join('');
  }

  async function clearOne(jobId) {
    try {
      await api(`/api/hf/downloads/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
      await queue()?.refresh?.();
      render();
    } catch (err) {
      toast(err.message || 'Could not remove that download', false);
    }
  }

  async function clearAll() {
    if (!historyJobs().length) return;
    if (!window.confirm('Clear all last downloads? Models already on disk stay installed.')) return;
    try {
      await api('/api/hf/downloads', { method: 'DELETE' });
      await queue()?.refresh?.();
      render();
    } catch (err) {
      toast(err.message || 'Could not clear download history', false);
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    document.querySelectorAll('[data-downloads-pane]').forEach((btn) => {
      btn.addEventListener('click', () => setPane(btn.dataset.downloadsPane));
    });
    document.getElementById('dfDownloadsClearAll')?.addEventListener('click', () => {
      void clearAll();
    });
    document.getElementById('dfDownloadsRange')?.addEventListener('change', (event) => {
      const next = String(event.target.value || 'all');
      range = ['1', '7', '30', '90', '365', 'all'].includes(next) ? next : 'all';
      localStorage.setItem(RANGE_KEY, range);
      render();
    });
    document.getElementById('dfDownloadsPageList')?.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-clear-job]');
      if (!btn) return;
      void clearOne(btn.dataset.clearJob);
    });
    queue()?.subscribe?.(render);
  }

  function onViewEnter() {
    bind();
    setPane(activeJobs().length ? 'active' : 'history');
    void queue()?.refresh?.({ discover: true }).then(() => {
      if (pane !== 'active' || !activeJobs().length) setPane('history');
      render();
    });
    render();
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashDownloadsLive = {
    onViewEnter,
    showPane: setPane,
    render,
  };
})();
