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

  let floatJobId = null;
  let floatMinimized = false;
  let floatDrag = null;

  function releaseModal() {
    const modal = document.getElementById('modelUpdateModal');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) document.body.classList.remove('modal-open');
  }

  function close() {
    releaseModal();
    document.getElementById('modelUpdateFloat')?.classList.add('hidden');
    document.getElementById('modelUpdatePill')?.classList.add('hidden');
    activeModel = null;
    floatJobId = null;
    floatMinimized = false;
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function progressHtml(job) {
    const pct = Math.max(0, Math.min(100, Number(job?.progress || 0)));
    const detail = escapeHtml(job?.detail || 'Updating model…');
    return `<p class="df-model-update-progress">${detail}</p><div class="df-model-update-progress-track"><span style="width:${pct}%"></span></div><p class="lm-setting-desc df-model-update-float-note">${Math.round(pct)}% complete. You can keep using the app. The previous model stays in place until the new files are verified.</p>`;
  }

  function syncPill(job, label) {
    const pill = document.getElementById('modelUpdatePill');
    const labelEl = document.getElementById('modelUpdatePillLabel');
    const pctEl = document.getElementById('modelUpdatePillPct');
    const fill = document.getElementById('modelUpdatePillFill');
    if (!pill) return;
    const pct = Math.max(0, Math.min(100, Number(job?.progress || 0)));
    const text = label || job?.detail || 'Updating model';
    if (labelEl) labelEl.textContent = text;
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
    if (fill) fill.style.width = `${pct}%`;
    pill.title = text;
    pill.classList.toggle('hidden', !floatMinimized);
  }

  function positionFloat(floatEl) {
    if (!floatEl || floatEl.dataset.placed === '1') return;
    const rect = floatEl.getBoundingClientRect();
    const width = rect.width || 420;
    const left = Math.max(12, window.innerWidth - width - 28);
    floatEl.style.left = `${left}px`;
    floatEl.style.top = '84px';
    floatEl.style.right = 'auto';
    floatEl.dataset.placed = '1';
  }

  function showFloat() {
    const floatEl = document.getElementById('modelUpdateFloat');
    if (!floatEl) return;
    floatMinimized = false;
    floatEl.classList.remove('hidden');
    positionFloat(floatEl);
    document.getElementById('modelUpdatePill')?.classList.add('hidden');
  }

  function minimizeFloat() {
    const floatEl = document.getElementById('modelUpdateFloat');
    if (!floatEl || floatEl.classList.contains('hidden') && !floatJobId) return;
    floatMinimized = true;
    floatEl.classList.add('hidden');
    const pill = document.getElementById('modelUpdatePill');
    pill?.classList.remove('hidden');
  }

  function openFloat(model, job) {
    releaseModal();
    const title = document.getElementById('modelUpdateFloatTitle');
    const body = document.getElementById('modelUpdateFloatBody');
    const name = model?.label || model?.filename || 'model';
    if (title) title.textContent = `Updating ${name}`;
    if (body) body.innerHTML = progressHtml(job || { detail: 'Starting the download…', progress: 0 });
    syncPill(job || { progress: 0 }, `Updating ${name}`);
    showFloat();
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

  function writeFloat(html, job, pillLabel) {
    const body = document.getElementById('modelUpdateFloatBody');
    if (body) body.innerHTML = html;
    if (job) syncPill(job, pillLabel);
  }

  function updateStillOpen() {
    const floatEl = document.getElementById('modelUpdateFloat');
    const pill = document.getElementById('modelUpdatePill');
    return Boolean(floatJobId)
      && (!floatEl?.classList.contains('hidden') || !pill?.classList.contains('hidden'));
  }

  async function poll(jobId) {
    try {
      const result = await api(`/api/models/update/${encodeURIComponent(jobId)}`, { timeoutMs: 15000 });
      const job = result?.job || {};
      const name = activeModel?.label || activeModel?.filename || 'model';
      if (job.status === 'done') {
        toast('Model update installed');
        await window.DFlashModelsLive?.refresh?.({ forceCatalogRefresh: true, rebindInspector: true });
        await window.DFlashServerLive?.refresh?.(true, { fresh: true });
        writeFloat(
          '<p class="df-model-update-success">The new model version is installed. The existing server profile and API address were preserved.</p>',
          { ...job, progress: 100 },
          `${name} updated`,
        );
        floatJobId = null;
        return;
      }
      if (job.status === 'error') {
        const message = job.detail || job.error || 'The update failed. The previous model was kept.';
        writeFloat(
          `<p class="df-model-update-error">${escapeHtml(message)}</p>`,
          job,
          'Update failed',
        );
        toast(job.error || 'Model update failed; the previous model was kept', false);
        floatJobId = null;
        return;
      }
      writeFloat(progressHtml(job), job, `Updating ${name}`);
      pollTimer = window.setTimeout(() => void poll(jobId), 1200);
    } catch (error) {
      if (updateStillOpen()) {
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
      floatJobId = result.job_id;
      openFloat(activeModel, { detail: 'Starting the download…', progress: 0 });
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
    document.getElementById('modelUpdateMinimize')?.addEventListener('click', minimizeFloat);
    document.getElementById('modelUpdatePill')?.addEventListener('click', showFloat);
    document.getElementById('modelUpdateFloatClose')?.addEventListener('click', () => {
      if (floatJobId) minimizeFloat();
      else close();
    });

    const head = document.getElementById('modelUpdateFloatHead');
    const floatEl = document.getElementById('modelUpdateFloat');
    head?.addEventListener('pointerdown', (event) => {
      if (event.target.closest('button') || !floatEl) return;
      const rect = floatEl.getBoundingClientRect();
      floatDrag = { dx: event.clientX - rect.left, dy: event.clientY - rect.top };
      try { head.setPointerCapture(event.pointerId); } catch { /* Pointer left before capture. */ }
    });
    head?.addEventListener('pointermove', (event) => {
      if (!floatDrag || !floatEl) return;
      const maxX = Math.max(8, window.innerWidth - floatEl.offsetWidth - 8);
      const maxY = Math.max(8, window.innerHeight - 48);
      const x = Math.min(maxX, Math.max(8, event.clientX - floatDrag.dx));
      const y = Math.min(maxY, Math.max(8, event.clientY - floatDrag.dy));
      floatEl.style.left = `${x}px`;
      floatEl.style.top = `${y}px`;
      floatEl.style.right = 'auto';
      floatEl.dataset.placed = '1';
    });
    const endDrag = () => { floatDrag = null; };
    head?.addEventListener('pointerup', endDrag);
    head?.addEventListener('pointercancel', endDrag);
  });

  window.DFlashModelUpdates = { setModels, open, close };
})();
