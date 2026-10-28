/** Hugging Face model update notification and staged-replacement wizard. */
(function () {
  const { api, toast } = window.ConsoleApi;
  let models = [];
  let activeModel = null;
  let pollTimer = null;

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function bytesLabel(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return 'Unknown';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let number = bytes;
    let index = 0;
    while (number >= 1024 && index < units.length - 1) {
      number /= 1024;
      index += 1;
    }
    return `${number >= 10 || index === 0 ? number.toFixed(0) : number.toFixed(1)} ${units[index]}`;
  }

  function revisionLabel(value) {
    const text = String(value || '').trim();
    return text ? text.slice(0, 12) : 'Unknown';
  }

  function dateLabel(value) {
    const date = new Date(value || '');
    return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleDateString();
  }

  function updateBadge() {
    const badge = document.getElementById('modelUpdatesBadge');
    if (!badge) return;
    const count = models.filter((model) => model?.hf_update_available === true).length;
    badge.classList.toggle('hidden', count === 0);
    badge.textContent = count === 1 ? '1 model update ready' : `${count} model updates ready`;
    badge.title = count
      ? 'Open Models filtered to verified Hugging Face updates'
      : 'No model updates ready';
  }

  function setModels(next) {
    models = Array.isArray(next) ? next : [];
    updateBadge();
  }

  function close() {
    const modal = document.getElementById('modelUpdateModal');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
    activeModel = null;
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function renderDetails(details) {
    const body = document.getElementById('modelUpdateBody');
    const confirm = document.getElementById('modelUpdateConfirm');
    if (!body) return;
    const files = Array.isArray(details?.files) ? details.files : [];
    const free = Number(details?.free_bytes || 0);
    const required = Number(details?.required_bytes || 0);
    const enough = details?.enough_space !== false;
    body.innerHTML = `
      <p class="df-model-update-lead">A newer verified version is ready. The Console will download it beside the current files, verify it, then replace the old files without changing this engine profile.</p>
      <div class="df-model-update-grid">
        <span>Current revision</span><strong>${escapeHtml(revisionLabel(details?.old_revision))}</strong>
        <span>New revision</span><strong class="df-model-update-new">${escapeHtml(revisionLabel(details?.new_revision))}</strong>
        <span>Hugging Face date</span><strong>${escapeHtml(dateLabel(details?.remote_last_modified))}</strong>
        <span>Download size</span><strong>${escapeHtml(bytesLabel(files.reduce((sum, item) => sum + Number(item?.size_bytes || 0), 0) || details?.remote_size_bytes))}</strong>
        <span>Free disk space</span><strong>${escapeHtml(bytesLabel(free))}</strong>
        <span>Required while replacing</span><strong>${escapeHtml(bytesLabel(required))}</strong>
      </div>
      <p class="lm-setting-desc">${enough ? 'The disk has enough space for the staged replacement.' : 'There is not enough free disk space for a safe staged replacement.'}</p>
      <div class="df-model-update-files"><strong>Files that will be replaced</strong><ul>${files.map((item) => `<li>${escapeHtml(item?.filename || '')} <span>${escapeHtml(bytesLabel(item?.size_bytes))}</span></li>`).join('')}</ul></div>
    `;
    if (confirm) confirm.disabled = !enough;
  }

  async function poll(jobId) {
    try {
      const result = await api(`/api/models/update/${encodeURIComponent(jobId)}`, { timeoutMs: 15000 });
      const job = result?.job || {};
      const body = document.getElementById('modelUpdateBody');
      const confirm = document.getElementById('modelUpdateConfirm');
      if (body) {
        body.innerHTML = `<p class="df-model-update-progress">${escapeHtml(job.detail || 'Updating model…')}</p><div class="df-model-update-progress-track"><span style="width:${Math.max(0, Math.min(100, Number(job.progress || 0)))}%"></span></div><p class="lm-setting-desc">${Math.round(Number(job.progress || 0))}% complete. The previous model remains protected until verification finishes.</p>`;
      }
      if (confirm) confirm.disabled = true;
      if (job.status === 'done') {
        toast('Model update installed');
        await window.DFlashModelsLive?.refresh?.({ forceCatalogRefresh: true, rebindInspector: true });
        await window.DFlashServerLive?.refresh?.(true, { fresh: true });
        if (body) body.innerHTML = '<p class="df-model-update-success">The new model version is installed. The existing server profile and API address were preserved.</p>';
        return;
      }
      if (job.status === 'error') {
        if (body) body.innerHTML = `<p class="df-model-update-error">${escapeHtml(job.detail || job.error || 'The update failed. The previous model was kept.')}</p>`;
        toast(job.error || 'Model update failed; the previous model was kept', false);
        return;
      }
      pollTimer = window.setTimeout(() => void poll(jobId), 1200);
    } catch (error) {
      if (document.getElementById('modelUpdateModal')?.classList.contains('open')) {
        pollTimer = window.setTimeout(() => void poll(jobId), 1800);
      } else {
        toast(error.message || 'Could not read model update status', false);
      }
    }
  }

  async function start() {
    if (!activeModel) return;
    const confirm = document.getElementById('modelUpdateConfirm');
    if (confirm) confirm.disabled = true;
    try {
      const result = await api('/api/models/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: activeModel.path,
          server_id: activeModel.server_id || null,
        }),
        timeoutMs: 30000,
      });
      if (!result?.job_id) throw new Error('The update job did not start');
      await poll(result.job_id);
    } catch (error) {
      if (confirm) confirm.disabled = false;
      toast(error.message || 'Could not start model update', false);
    }
  }

  async function open(model) {
    if (!model?.path) return;
    activeModel = model;
    const modal = document.getElementById('modelUpdateModal');
    const body = document.getElementById('modelUpdateBody');
    const confirm = document.getElementById('modelUpdateConfirm');
    const title = document.getElementById('modelUpdateTitle');
    if (!modal || !body) return;
    if (title) title.textContent = `Update ${model.label || model.filename || 'model'}`;
    body.innerHTML = '<p>Checking the latest Hugging Face revision…</p>';
    if (confirm) {
      confirm.disabled = true;
      confirm.textContent = 'Download and replace';
    }
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    try {
      const query = new URLSearchParams({ path: model.path });
      if (model.server_id) query.set('server_id', model.server_id);
      const result = await api(`/api/models/update/details?${query.toString()}`, { timeoutMs: 30000 });
      renderDetails(result?.details || {});
    } catch (error) {
      body.innerHTML = `<p class="df-model-update-error">${escapeHtml(error.message || 'No update is available for this model.')}</p>`;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('modelUpdatesBadge')?.addEventListener('click', () => {
      window.DFlashModelsLive?.showUpdatesFilter?.();
    });
    document.getElementById('modelUpdateConfirm')?.addEventListener('click', () => void start());
    document.getElementById('modelUpdateCancel')?.addEventListener('click', close);
    document.getElementById('modelUpdateClose')?.addEventListener('click', close);
  });

  window.DFlashModelUpdates = { setModels, open, close };
})();
