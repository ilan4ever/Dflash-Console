/** Dedicated Downloads page — current transfers and last finished jobs. */
(function () {
  const { api, toast } = window.ConsoleApi;

  const RANGE_KEY = 'dflashConsole.downloadsRange';
  let pane = 'active';
  let bound = false;
  let range = localStorage.getItem(RANGE_KEY) || 'all';
  let selectedJobId = '';
  const modelByJobId = new Map();

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

  function modelsLive() {
    return window.DFlashModelsLive;
  }

  function serverLive() {
    return window.DFlashServerLive;
  }

  function allJobs() {
    return queue()?.getJobs?.() || [];
  }

  function activeJobs() {
    return allJobs().filter((job) => job.status === 'downloading' || job.status === 'incomplete');
  }

  function historyJobs() {
    const cutoff = rangeCutoff();
    return allJobs()
      .filter((job) => job.status !== 'downloading' && job.status !== 'incomplete')
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

  function jobById(jobId) {
    const key = String(jobId || '').trim();
    if (!key) return null;
    return allJobs().find((job) => String(job.id || '') === key) || null;
  }

  function canLoadJob(job) {
    if (!job?.path) return false;
    return job.status === 'done';
  }

  async function resolveJobModel(job) {
    if (!job?.id) return null;
    const cached = modelByJobId.get(job.id);
    if (cached) return cached;
    const meta = queue()?.getJobMeta?.(job) || {};
    const model = await modelsLive()?.ensureModelForDownload?.(job, meta);
    if (model) modelByJobId.set(job.id, model);
    return model || null;
  }

  function loadActionsHtml(job, model) {
    if (!canLoadJob(job) || !model) return '';
    const actions = modelsLive()?.renderLoadActions?.(model) || '';
    if (!actions) return '';
    return `<div class="df-downloads-card-actions">${actions}</div>`;
  }

  async function bindInspectorForJob(job) {
    if (!job) return;
    const model = await resolveJobModel(job);
    if (!model) return;
    if (serverLive()?.flushInspectorSave) {
      await serverLive().flushInspectorSave();
    }
    if (serverLive()?.applyModelSelection) {
      await serverLive().applyModelSelection(model);
    }
    serverLive()?.ensureInspectorVisible?.();
    serverLive()?.focusInspectorTab?.('load');
  }

  async function selectDownloadJob(jobId) {
    const job = jobById(jobId);
    if (!job) return;
    selectedJobId = job.id;
    await bindInspectorForJob(job);
    render();
  }

  async function loadDownloadJob(jobId) {
    const job = jobById(jobId);
    if (!job) return;
    if (!canLoadJob(job)) {
      toast('Finish the download before loading this model.', false);
      return;
    }
    const model = await resolveJobModel(job);
    if (!model) {
      toast('This model is not available to load yet.', false);
      return;
    }
    selectedJobId = job.id;
    await bindInspectorForJob(job);
    await modelsLive()?.loadModel?.(model);
    render();
  }

  function setPane(next) {
    pane = next === 'history' ? 'history' : 'active';
    document.querySelectorAll('[data-downloads-pane]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.downloadsPane === pane);
    });
    void render();
  }

  async function render() {
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
        ? `${active.length} downloading / incomplete now`
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

    const cards = await Promise.all(rows.map(async (job) => {
      const model = canLoadJob(job) ? await resolveJobModel(job) : null;
      const remove = job.status === 'downloading'
        ? ''
        : `<button type="button" class="lm-icon-btn tiny df-downloads-remove" data-clear-job="${escapeHtml(job.id)}" title="Remove from last downloads" aria-label="Remove from last downloads">×</button>`;
      const selectedClass = job.id === selectedJobId ? ' is-selected' : '';
      const cardHtml = queue()?.renderDownloadCardHtml?.(job, {
        variant: 'page',
        removeButtonHtml: remove,
        loadActionsHtml: loadActionsHtml(job, model),
        selectedClass,
      }) || '';
      return cardHtml;
    }));
    list.innerHTML = cards.join('');
  }

  async function clearOne(jobId) {
    try {
      await api(`/api/hf/downloads/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
      if (selectedJobId === jobId) selectedJobId = '';
      modelByJobId.delete(jobId);
      await queue()?.refresh?.();
      await render();
    } catch (err) {
      toast(err.message || 'Could not remove that download', false);
    }
  }

  async function clearAll() {
    if (!historyJobs().length) return;
    if (!window.confirm('Clear all last downloads? Models already on disk stay installed.')) return;
    try {
      await api('/api/hf/downloads', { method: 'DELETE' });
      selectedJobId = '';
      modelByJobId.clear();
      await queue()?.refresh?.();
      await render();
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
      void render();
    });
    document.getElementById('dfDownloadsPageList')?.addEventListener('click', (event) => {
      const resumeBtn = event.target.closest('[data-resume-job]');
      if (resumeBtn) {
        void queue()?.resumeDownloadJob?.(resumeBtn.dataset.resumeJob);
        return;
      }
      const loadBtn = event.target.closest('[data-action="load-model"]');
      if (loadBtn) {
        event.preventDefault();
        event.stopPropagation();
        const card = loadBtn.closest('.df-downloads-card');
        void loadDownloadJob(card?.dataset?.downloadJobId || '');
        return;
      }
      const loadLlmBtn = event.target.closest('[data-action="load-llm"]');
      if (loadLlmBtn) {
        event.preventDefault();
        event.stopPropagation();
        const card = loadLlmBtn.closest('.df-downloads-card');
        const job = jobById(card?.dataset?.downloadJobId || '');
        if (!job) return;
        void (async () => {
          const model = await resolveJobModel(job);
          if (!model) return;
          selectedJobId = job.id;
          await bindInspectorForJob(job);
          await modelsLive()?.loadModel?.(model, { llmOnly: true });
          await render();
        })();
        return;
      }
      const unloadBtn = event.target.closest('[data-action="unload-model"]');
      if (unloadBtn) {
        event.preventDefault();
        event.stopPropagation();
        const card = unloadBtn.closest('.df-downloads-card');
        const job = jobById(card?.dataset?.downloadJobId || '');
        if (!job) return;
        void (async () => {
          const model = await resolveJobModel(job);
          if (!model) return;
          await modelsLive()?.unloadModel?.(model);
          await render();
        })();
        return;
      }
      if (event.target.closest('[data-engine-pick]')) return;
      const btn = event.target.closest('[data-clear-job]');
      if (btn) {
        void clearOne(btn.dataset.clearJob);
        return;
      }
      const card = event.target.closest('.df-downloads-card');
      if (!card) return;
      void selectDownloadJob(card.dataset.downloadJobId || '');
    });
    queue()?.subscribe?.(() => {
      void render();
    });
  }

  async function onViewEnter() {
    bind();
    serverLive()?.ensureInspectorVisible?.();
    serverLive()?.focusInspectorTab?.('load');
    const nextPane = activeJobs().length ? 'active' : 'history';
    pane = nextPane;
    document.querySelectorAll('[data-downloads-pane]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.downloadsPane === pane);
    });
    try {
      await queue()?.refresh?.({ discover: true });
    } catch {
      /* keep rendering with cached jobs */
    }
    if (pane === 'active' && !activeJobs().length) {
      setPane('history');
    } else {
      const firstLoadable = historyJobs().find((job) => canLoadJob(job));
      if (firstLoadable && !selectedJobId) {
        selectedJobId = firstLoadable.id;
        await bindInspectorForJob(firstLoadable);
      }
      await render();
    }
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashDownloadsLive = {
    onViewEnter,
    showPane: setPane,
    render,
    selectDownloadJob,
    loadDownloadJob,
    resolveJobModel,
  };
})();
