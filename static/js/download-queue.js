/** Global Hugging Face download queue — pinned to the left sidebar footer. */
(function () {
  const { api, toast } = window.ConsoleApi;

  const jobs = new Map();
  const labels = new Map();
  const metaById = new Map();
  const listeners = new Set();
  let pollTimer = null;
  let panelOpen = false;
  let downloadContextJob = null;

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

  function formatSpeed(bps) {
    const bytes = Number(bps);
    if (!Number.isFinite(bytes) || bytes <= 0) return '';
    return `${formatBytes(bytes)}/s`;
  }

  function formatEta(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return '';
    if (value < 60) return `${Math.max(1, Math.round(value))}s left`;
    if (value < 3600) return `${Math.max(1, Math.round(value / 60))}m left`;
    return `${(value / 3600).toFixed(1)}h left`;
  }

  function formatElapsed(startedAt) {
    const start = Number(startedAt);
    if (!Number.isFinite(start) || start <= 0) return '';
    let secs = Math.max(0, Math.floor(Date.now() / 1000 - start));
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    secs %= 60;
    if (mins < 60) return `${mins}m ${secs}s`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  }

  function progressLabel(job) {
    const elapsed = job?.status === 'downloading' ? formatElapsed(job.started_at) : '';
    if (job?.retrying) {
      return elapsed ? `Reconnecting… · ${elapsed}` : 'Reconnecting…';
    }
    const total = Number(job?.bytes_total);
    const read = Number(job?.bytes_read);
    const shardTotal = Number(job?.shard_total);
    const shardPresent = Number(job?.shard_present);
    const done = job?.status === 'done';
    let main = 'Starting…';
    if (shardTotal > 1 && shardPresent >= 0) {
      const shardPct = Math.max(0, Math.min(done ? 100 : 99, Math.round((shardPresent / shardTotal) * 100)));
      main = `${shardPresent}/${shardTotal} shards (${shardPct}%)`;
    } else if (total > 0 && read > 0) {
      const raw = (read / total) * 100;
      const pct = done ? Math.min(100, Math.round(raw)) : Math.max(0, Math.min(99, Math.round(raw)));
      main = `${pct}%`;
    } else if (read > 0) {
      main = formatBytes(read);
    } else if (total > 0) {
      main = 'Starting…';
    } else {
      const pct = Number(job?.progress);
      if (Number.isFinite(pct) && pct > 0) {
        main = `${done ? Math.min(100, Math.round(pct)) : Math.min(99, Math.round(pct))}%`;
      }
    }
    return elapsed ? `${main} · ${elapsed}` : main;
  }

  function progressWidth(job) {
    const total = Number(job?.bytes_total);
    const read = Number(job?.bytes_read);
    const shardTotal = Number(job?.shard_total);
    const shardPresent = Number(job?.shard_present);
    const done = job?.status === 'done';
    if (shardTotal > 1 && shardPresent >= 0) {
      return Math.max(2, Math.min(done ? 100 : 99, (shardPresent / shardTotal) * 100));
    }
    if (total > 0 && read > 0) {
      const raw = (read / total) * 100;
      return Math.max(2, Math.min(done ? 100 : 99, raw));
    }
    const pct = Number(job?.progress);
    if (Number.isFinite(pct) && pct > 0) return Math.max(2, Math.min(done ? 100 : 99, pct));
    return null;
  }

  function inferAuthor(repoId) {
    const id = String(repoId || '').trim();
    return id.includes('/') ? id.split('/')[0] : id;
  }

  function inferQuant(filename) {
    const name = String(filename || '');
    const match = name.match(/(?:^|[._-])(Q\d[_A-Z0-9]*|F16|BF16|IQ\d[_A-Z0-9]*)(?:[._-]|\.|$)/i);
    return match ? match[1].toUpperCase() : '';
  }

  function inferFormat(filename, stored) {
    if (stored) return stored;
    const lower = String(filename || '').toLowerCase();
    if (lower.endsWith('.gguf')) return 'GGUF';
    if (lower.endsWith('.safetensors')) return 'Safetensors';
    return '';
  }

  function shortPath(path) {
    const value = String(path || '');
    if (value.length <= 72) return value;
    return `…${value.slice(-68)}`;
  }

  function hfRepoUrl(repoId) {
    const id = String(repoId || '').trim();
    return id.includes('/') ? `https://huggingface.co/${id}` : '';
  }

  function jobIdentifier(job) {
    const repo = String(job?.repo_id || '').trim();
    const file = String(job?.filename || '').trim();
    if (repo && file) return `${repo} · ${file}`;
    if (repo) return repo;
    if (file) return file;
    return String(job?.id || '').trim();
  }

  function hideDownloadContextMenu() {
    const menu = document.getElementById('downloadsContextMenu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML = '';
    downloadContextJob = null;
  }

  function positionContextMenu(menu, event) {
    const margin = 8;
    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');
    const rect = menu.getBoundingClientRect();
    let left = event.clientX;
    let top = event.clientY;
    if (left + rect.width + margin > window.innerWidth) {
      left = Math.max(margin, window.innerWidth - rect.width - margin);
    }
    if (top + rect.height + margin > window.innerHeight) {
      top = Math.max(margin, event.clientY - rect.height - margin);
    }
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  async function removeDownloadJob(jobId) {
    try {
      await api(`/api/hf/downloads/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
      await refresh();
      window.DFlashDownloadsLive?.render?.();
      toast('Removed from last downloads');
    } catch (err) {
      toast(err.message || 'Could not remove that download', false);
    }
  }

  async function resumeDownloadJob(jobId) {
    const id = String(jobId || '').trim();
    if (!id) return;
    const existing = jobs.get(id);
    if (existing) {
      jobs.set(id, {
        ...existing,
        status: 'downloading',
        speed_bps: 0,
        eta_seconds: null,
        error: null,
        detail: 'Resuming…',
        finished_at: null,
      });
      emit();
      window.DFlashDownloadsLive?.render?.();
    }
    try {
      toast('Resuming download…');
      const data = await api('/api/hf/downloads/resume', {
        method: 'POST',
        body: JSON.stringify({ job_id: id }),
      });
      if (data?.job_id && data.job_id !== id) {
        const old = jobs.get(id);
        if (old) {
          labels.set(data.job_id, labels.get(id) || old.repo_id || old.filename || 'Model');
        }
      }
      await refresh({ discover: true });
      ensurePolling();
      window.DFlashDownloadsLive?.showPane?.('active');
      window.DFlashDownloadsLive?.render?.();
      toast(data?.already_running ? 'Download already running' : 'Download resumed');
    } catch (err) {
      if (existing) {
        jobs.set(id, existing);
        emit();
        window.DFlashDownloadsLive?.render?.();
      }
      toast(err.message || 'Could not resume download', false);
    }
  }

  async function runDownloadContextCommand(cmd, job) {
    if (!job) return;
    const repoId = String(job.repo_id || '').trim();
    const filename = String(job.filename || '').trim();
    const path = String(job.path || '').trim();
    const hfUrl = hfRepoUrl(repoId);
    const identifier = jobIdentifier(job);

    if (cmd === 'resume') {
      void resumeDownloadJob(job.id);
      return;
    }
    if (cmd === 'copy-id') {
      if (!identifier) return;
      await navigator.clipboard.writeText(identifier);
      toast('Identifier copied');
      return;
    }
    if (cmd === 'copy-url') {
      if (!hfUrl) return;
      await navigator.clipboard.writeText(hfUrl);
      toast('Hugging Face URL copied');
      return;
    }
    if (cmd === 'copy-path') {
      if (!path) return;
      await navigator.clipboard.writeText(path);
      toast('File path copied');
      return;
    }
    if (cmd === 'copy-filename') {
      if (!filename) return;
      await navigator.clipboard.writeText(filename);
      toast('Filename copied');
      return;
    }
    if (cmd === 'open-hf') {
      if (hfUrl) window.open(hfUrl, '_blank', 'noopener,noreferrer');
      return;
    }
    if (cmd === 'metadata') {
      const modal = document.getElementById('modelMetadataModal');
      const pre = document.getElementById('modelMetadataBody');
      if (pre) {
        pre.textContent = JSON.stringify({
          job,
          meta: getJobMeta(job),
          label: labels.get(job.id) || '',
        }, null, 2);
      }
      modal?.classList.add('open');
      modal?.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      return;
    }
    if (cmd === 'open-catalog') {
      if (!repoId) {
        toast('No Hugging Face repo for this download', false);
        return;
      }
      window.DFlashShell?.setView?.('catalog');
      await window.DFlashModelSearchLive?.revealRepo?.(repoId, { preferCache: true, backgroundDetail: true });
      return;
    }
    if (cmd === 'goto-library') {
      if (!path) {
        toast('No local file path for this download yet', false);
        return;
      }
      window.DFlashShell?.setView?.('models');
      const found = await window.DFlashModelsLive?.revealModelFromEngineCard?.({
        path,
        modelId: repoId || filename,
        label: labels.get(job.id) || filename || repoId,
      });
      if (!found) toast('This model is not in the Model library', false);
      return;
    }
    if (cmd === 'load-model') {
      if (job.status !== 'done') {
        toast('Finish the download before loading this model.', false);
        return;
      }
      window.DFlashShell?.setView?.('downloads');
      await window.DFlashDownloadsLive?.loadDownloadJob?.(job.id);
      return;
    }
    if (cmd === 'open-downloads') {
      window.DFlashDownloadsLive?.showPane?.(job.status === 'downloading' ? 'active' : 'history');
      window.DFlashShell?.setView?.('downloads');
      return;
    }
    if (cmd === 'remove') {
      if (job.status === 'downloading') return;
      await removeDownloadJob(job.id);
    }
  }

  function openDownloadContextMenu(event, job) {
    const menu = document.getElementById('downloadsContextMenu');
    if (!menu || !job?.id) return;
    downloadContextJob = job;
    const repoId = String(job.repo_id || '').trim();
    const filename = String(job.filename || '').trim();
    const path = String(job.path || '').trim();
    const hfUrl = hfRepoUrl(repoId);
    const identifier = jobIdentifier(job);
    const isActive = job.status === 'downloading';
    const canResume = job.status === 'incomplete' || (job.status === 'error' && job.resumable);

    const canLoad = job.status === 'done' && !!path;
    menu.innerHTML = `
      <button type="button" data-cmd="resume"${canResume ? '' : ' disabled'}>Resume download</button>
      <button type="button" data-cmd="load-model"${canLoad ? '' : ' disabled'}>Load model</button>
      <button type="button" data-cmd="copy-id"${identifier ? '' : ' disabled'}>Copy identifier</button>
      <button type="button" data-cmd="copy-url"${hfUrl ? '' : ' disabled'}>Copy Hugging Face URL</button>
      <button type="button" data-cmd="copy-path"${path ? '' : ' disabled'}>Copy file path</button>
      <button type="button" data-cmd="copy-filename"${filename ? '' : ' disabled'}>Copy filename</button>
      <button type="button" data-cmd="open-hf"${hfUrl ? '' : ' disabled'}>Open Hugging Face</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <hr>
      <button type="button" data-cmd="open-catalog"${repoId ? '' : ' disabled'}>Open in Model catalog</button>
      <button type="button" data-cmd="goto-library"${path ? '' : ' disabled'}>Go to model in Model library</button>
      <button type="button" data-cmd="open-downloads">Open Downloads page</button>
      <hr>
      <button type="button" data-cmd="remove"${isActive ? ' disabled' : ''}>Remove from last downloads</button>`;

    positionContextMenu(menu, event);
    menu.querySelectorAll('button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', (clickEvent) => {
        clickEvent.stopPropagation();
        void runDownloadContextCommand(btn.dataset.cmd, job);
        hideDownloadContextMenu();
      });
    });
  }

  function handleDownloadCardContextMenu(event) {
    const card = event.target.closest('.df-downloads-card');
    if (!card) return;
    const jobId = card.dataset.downloadJobId || '';
    const job = jobs.get(jobId);
    if (!job) return;
    event.preventDefault();
    event.stopPropagation();
    openDownloadContextMenu(event, job);
  }

  const MODALITY_BADGES = {
    llm: ['LLM', 'blue'],
    embedding: ['Embed', 'purple'],
    'speech-to-text': ['STT', 'green'],
    'text-to-speech': ['TTS', 'green'],
    vision: ['Vision', 'purple'],
    ocr: ['OCR', 'yellow'],
    translation: ['Translate', 'blue'],
  };

  function lmTag(label, tone = 'blue', title = '') {
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
    // Match model-card modality badges so Downloads and Models look the same.
    return `<span class="lm-badge ${tone}"${titleAttr}>${escapeHtml(label)}</span>`;
  }

  function dflashLogoLabel(label = 'DFlash 1') {
    const safeLabel = escapeHtml(label);
    return `<span class="lm-tag gold dflash-logo-label" role="img" aria-label="${safeLabel}" title="${safeLabel}"></span>`;
  }

  function inferJobModality(job, meta) {
    const stored = String(meta?.modality || job?.modality || '').trim().toLowerCase();
    if (stored) return stored;
    const hay = [
      job?.repo_id,
      job?.filename,
      meta?.pipeline_tag,
      meta?.pipeline,
      job?.kind,
    ].join(' ').toLowerCase();
    if (job?.kind === 'vision' || /mmproj|vision|multimodal|-vl-/.test(hay)) return 'vision';
    if (/whisper|speech-to-text|\bstt\b/.test(hay)) return 'speech-to-text';
    if (/piper|text-to-speech|\btts\b/.test(hay)) return 'text-to-speech';
    if (/embed|nomic|feature-extraction/.test(hay)) return 'embedding';
    if (/ocr|chandra|image-to-text/.test(hay)) return 'ocr';
    if (/translate|translation|nllb|opus-mt/.test(hay)) return 'translation';
    if (String(job?.filename || '').toLowerCase().endsWith('.gguf')) return 'llm';
    return 'llm';
  }

  function jobModelShape(job, meta) {
    const totalBytes = Number(job?.bytes_total);
    const sizeGb = Number.isFinite(totalBytes) && totalBytes > 0
      ? totalBytes / (1024 ** 3)
      : (Number(meta?.size_gb) || Number(job?.size_gb) || undefined);
    return {
      filename: job?.filename || '',
      label: job?.repo_id || labels.get(job?.id) || '',
      repo_id: job?.repo_id || '',
      path: job?.path || '',
      size_gb: sizeGb,
      modality: inferJobModality(job, meta),
      // Full HF repos in Downloads are target models unless explicitly marked.
      accelerator_only: meta?.accelerator_only ?? job?.accelerator_only ?? (
        String(job?.kind || '').toLowerCase() === 'repo' ? false : undefined
      ),
      reasoning: meta?.reasoning ?? job?.reasoning,
      capabilities: meta?.capabilities || job?.capabilities || [],
      pipeline_tag: meta?.pipeline_tag || job?.pipeline_tag || '',
      dflash_generation_label: meta?.dflash_generation_label || job?.dflash_generation_label || '',
    };
  }

  function isDownloadAccelerator(job, meta) {
    if (meta?.accelerator_only === true || job?.accelerator_only === true) return true;
    if (meta?.accelerator_only === false || job?.accelerator_only === false) return false;
    const model = jobModelShape(job, meta);
    return window.DFlashModelGroups?.isAcceleratorOnlyModel?.(model) === true;
  }

  function isConsoleLibraryJob(job, model) {
    if (window.DFlashModelGroups?.isConsoleDiskPath?.(model)) return true;
    const path = String(job?.path || model?.path || '').replace(/\\/g, '/').toLowerCase();
    return path.includes('/dflash-console/') || path.includes('/dflash console/');
  }

  function downloadJobTagsHtml(job, meta) {
    const model = jobModelShape(job, meta);
    const tags = [];
    const modality = String(model.modality || '').trim().toLowerCase();
    const modEntry = MODALITY_BADGES[modality]
      || window.DFlashModelCard?.MODALITY_BADGES?.[modality];
    if (modEntry) {
      const label = Array.isArray(modEntry) ? modEntry[0] : modEntry;
      const tone = Array.isArray(modEntry) ? modEntry[1] : 'blue';
      tags.push(lmTag(label, tone, `Modality: ${modality}`));
    } else if (window.DFlashModelCard?.classificationTags) {
      // Fall back for unknown modalities, but strip false accelerator tags below.
      const raw = window.DFlashModelCard.classificationTags(model);
      if (raw) tags.push(raw);
    }

    // Console library destination badge — independent of Accelerator.
    if (isConsoleLibraryJob(job, model)) {
      tags.push(dflashLogoLabel('DFlash Console library'));
    }

    if (isDownloadAccelerator(job, meta)) {
      const gen = model.dflash_generation_label
        || window.DFlashModelGroups?.acceleratorGenerationLabel?.(model)
        || 'DFlash 1';
      if (!tags.some((html) => html.includes('dflash-logo-label'))) {
        tags.push(dflashLogoLabel(`${gen} accelerator`));
      }
      tags.push(lmTag('Accelerator', 'orange', `${gen} draft accelerator; not a target model`));
    }

    // Deduplicate if classificationTags already injected the same markup.
    return [...new Set(tags.filter(Boolean))].join('');
  }

  function getJobMeta(job) {
    const stored = metaById.get(job?.id) || {};
    const filename = job?.filename || '';
    const repoId = job?.repo_id || '';
    const author = stored.author || inferAuthor(repoId);
    const quant = stored.quant || inferQuant(filename);
    const format = inferFormat(filename, stored.format || stored.pipeline_tag || stored.pipeline || job?.pipeline_tag || '');
    const sizeFromBytes = job?.bytes_total ? formatBytes(job.bytes_total) : '';
    const diskBytes = Number(job?.disk_bytes);
    const hasDiskBytes = Number.isFinite(diskBytes) && diskBytes > 0;
    const sizeLabel = stored.size_label || (hasDiskBytes ? formatBytes(diskBytes) : sizeFromBytes) || '';
    const modality = inferJobModality(job, stored);
    return {
      lab: stored.lab || '',
      author,
      quant,
      format,
      sizeLabel,
      modality,
      pipeline_tag: stored.pipeline_tag || job?.pipeline_tag || '',
      accelerator_only: stored.accelerator_only ?? job?.accelerator_only,
      reasoning: stored.reasoning ?? job?.reasoning,
      capabilities: stored.capabilities || job?.capabilities || [],
      dflash_generation_label: stored.dflash_generation_label || job?.dflash_generation_label || '',
    };
  }

  function renderDownloadCardHtml(job, { variant = 'panel', removeButtonHtml = '', loadActionsHtml = '', selectedClass = '' } = {}) {
    const meta = getJobMeta(job);
    const diskBytes = Number(job?.disk_bytes);
    const hasDiskBytes = Number.isFinite(diskBytes) && diskBytes > 0;
    const title = labels.get(job.id) || job.repo_id || job.filename || 'Model';
    const showRepo = job.repo_id && job.repo_id !== title;
    const width = progressWidth(job);
    const indeterminate = job.status === 'downloading' && width == null;
    const fillStyle = width != null ? ` style="width:${width}%"` : '';
    const fillClass = indeterminate ? ' is-indeterminate' : '';
    const speed = job.status === 'downloading' ? formatSpeed(job.speed_bps) : '';
    const eta = job.status === 'downloading' ? formatEta(job.eta_seconds) : '';
    const readBytes = hasDiskBytes
      ? diskBytes
      : Number(job?.bytes_read || 0);
    const totalBytes = Number(job?.bytes_total || 0);
    const bytes = totalBytes
      ? `${formatBytes(readBytes)} / ${formatBytes(totalBytes)}`
      : (readBytes ? formatBytes(readBytes) : '');
    const descParts = [meta.lab, meta.author, meta.quant, meta.format].filter(Boolean);
    const descLine = descParts.length
      ? descParts.join(' · ')
      : (meta.sizeLabel || '');
    const displaySize = totalBytes && readBytes > 0 && readBytes < totalBytes
      ? `${formatBytes(readBytes)} on disk`
      : (meta.sizeLabel || (totalBytes ? formatBytes(totalBytes) : ''));
    const sizeStat = displaySize && job.status !== 'downloading' ? displaySize : '';
    const statusPrimary = job.status === 'downloading'
      ? progressLabel(job)
      : (job.status === 'incomplete'
        ? (job.shard_total
          ? `Incomplete ${job.shard_present || 0}/${job.shard_total}`
          : (totalBytes && readBytes > 0
            ? `${Math.min(99, Math.round((readBytes / Math.max(totalBytes, readBytes)) * 100))}% on disk`
            : 'Incomplete — Resume'))
        : (job.status === 'done' ? 'Complete' : 'Failed'));
    const statusClass = job.status === 'downloading'
      ? ''
      : (job.status === 'incomplete'
        ? ' is-error'
        : (job.status === 'done' ? ' is-done' : ' is-error'));
    const asideBytesTotal = totalBytes > 0 && readBytes > totalBytes ? Math.max(totalBytes, readBytes) : totalBytes;
    const asideBytes = asideBytesTotal
      ? `${formatBytes(readBytes)} / ${formatBytes(asideBytesTotal)}`
      : bytes;
    const asideSecondary = job.status === 'downloading'
      ? [asideBytes, [speed, eta].filter(Boolean).join(' · ')].filter(Boolean)
      : [
        sizeStat || (totalBytes ? formatBytes(readBytes || totalBytes) : ''),
        variant === 'page' && job.finished_at
          ? new Date(Number(job.finished_at) * 1000).toLocaleString()
          : '',
      ].filter(Boolean);
    const footLine = job.status === 'downloading'
      ? shortPath(job.path)
      : (job.status === 'error'
        ? String(job.error || job.path || '')
        : (job.status === 'incomplete'
          ? String(job.error || shortPath(job.path || job.repo_id || ''))
          : shortPath(job.path || job.repo_id || '')));
    const bar = job.status === 'downloading'
      ? `<div class="df-downloads-item-bar"><div class="df-downloads-item-fill${fillClass}"${fillStyle}></div></div>`
      : (job.status === 'incomplete' && (job.shard_total || (totalBytes && readBytes > 0))
        ? `<div class="df-downloads-item-bar"><div class="df-downloads-item-fill" style="width:${Math.max(1, Math.min(99, job.shard_total
          ? Math.round(((Number(job.shard_present) || 0) / Number(job.shard_total)) * 100)
          : Math.round((readBytes / totalBytes) * 100)))}%"></div></div>`
        : '');
    const resumeBtn = job.status === 'incomplete'
      ? `<button type="button" class="lm-btn ghost tiny df-downloads-resume" data-resume-job="${escapeHtml(job.id)}" title="Resume downloading remaining files">Resume</button>`
      : '';
    const tagsHtml = downloadJobTagsHtml(job, meta);
    const tags = tagsHtml
      ? `<div class="df-downloads-card-tags">${tagsHtml}</div>`
      : '';
    const cardDetails = window.DFlashModelCard?.detailsHtml?.(jobModelShape(job, meta), {
      includeTarget: false,
      includeAccelerator: true,
      alwaysForStack: false,
    }) || '';
    const avatar = meta.author
      ? `<span class="df-downloads-card-avatar" aria-hidden="true">${escapeHtml(meta.author.charAt(0).toUpperCase())}</span>`
      : '';
    const wrapperClass = variant === 'page' ? 'df-downloads-page-item' : 'df-downloads-item';
    const pageSelectedClass = variant === 'page' ? String(selectedClass || '') : '';
    return `
      <div class="${wrapperClass} df-downloads-card${job.status === 'error' || job.status === 'incomplete' ? ' is-error' : ''}${job.status === 'done' ? ' is-done' : ''}${pageSelectedClass}" data-download-job-id="${escapeHtml(job.id)}">
        ${removeButtonHtml}
        <div class="df-downloads-card-body">
          ${avatar}
          <div class="df-downloads-card-main">
            <div class="df-downloads-card-title-row">
              <span class="df-downloads-card-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
              ${resumeBtn}
            </div>
            ${showRepo ? `<div class="df-downloads-card-repo">${escapeHtml(job.repo_id)}</div>` : ''}
            ${job.filename ? `<div class="df-downloads-card-file">${escapeHtml(job.filename)}</div>` : ''}
            ${descLine ? `<div class="df-downloads-card-desc">${escapeHtml(descLine)}</div>` : ''}
            ${footLine ? `<div class="df-downloads-card-foot">${escapeHtml(footLine)}</div>` : ''}
            ${cardDetails}
            ${variant === 'page' ? loadActionsHtml : ''}
          </div>
          ${tags}
          <div class="df-downloads-card-aside">
            <span class="df-downloads-card-stat-primary${statusClass}">${escapeHtml(statusPrimary)}</span>
            ${asideSecondary.map((line) => `<span class="df-downloads-card-stat">${escapeHtml(line)}</span>`).join('')}
          </div>
        </div>
        ${bar}
      </div>`;
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
    return sortedJobs().filter((job) => job.status === 'downloading' || job.status === 'incomplete');
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
    const active = activeJobs();
    renderBadge(active);
    renderStatusFeed(active);
  }

  function aggregateProgress(active) {
    let read = 0;
    let total = 0;
    active.forEach((job) => {
      const jobTotal = Number(job.bytes_total);
      const jobRead = Number(job.bytes_read);
      if (jobTotal > 0) {
        total += jobTotal;
        read += Math.min(Math.max(0, jobRead), jobTotal);
      }
    });
    if (total <= 0) {
      return { width: null, label: active.some((job) => job.retrying) ? '…' : '' };
    }
    if (read <= 0) {
      return { width: 0, label: active.some((job) => job.retrying) ? '…' : '0%' };
    }
    const pct = Math.max(1, Math.min(100, Math.round((read / total) * 100)));
    return { width: pct, label: `${pct}%` };
  }

  function renderBadge(active) {
    const count = Array.isArray(active) ? active.length : Number(active) || 0;
    const jobs = Array.isArray(active) ? active : activeJobs();
    const badge = badgeEl();
    const toggle = toggleEl();
    const tray = trayEl();
    const progressWrap = document.getElementById('dfDownloadsToggleProgress');
    const progressFill = document.getElementById('dfDownloadsToggleProgressFill');
    const progressPct = document.getElementById('dfDownloadsToggleProgressPct');
    if (!badge || !toggle || !tray) return;
    badge.textContent = String(count);
    badge.classList.toggle('hidden', count <= 0);
    badge.toggleAttribute('data-count-wide', count >= 10);
    tray.classList.toggle('has-active', count > 0);
    toggle.classList.toggle('is-active', count > 0);

    if (!progressWrap || !progressFill || !progressPct) return;
    if (count <= 0) {
      progressWrap.classList.add('hidden');
      progressFill.classList.remove('is-indeterminate');
      progressFill.style.width = '';
      progressPct.textContent = '';
      return;
    }

    progressWrap.classList.remove('hidden');
    const agg = aggregateProgress(jobs);
    const indeterminate = agg.width == null;
    progressFill.classList.toggle('is-indeterminate', indeterminate);
    if (indeterminate) {
      progressFill.style.width = '';
    } else {
      progressFill.style.width = `${agg.width}%`;
    }
    progressPct.textContent = agg.label || (indeterminate ? '…' : '');
  }

  function renderStatusFeed(active) {
    if (!active.length) return;
    const speed = active.length === 1 ? formatSpeed(active[0].speed_bps) : '';
    const primary = active.length === 1
      ? `Downloading ${labels.get(active[0].id) || active[0].filename || 'model'} · ${progressLabel(active[0])}${speed ? ` · ${speed}` : ''}`
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

    list.innerHTML = rows.map((job) => renderDownloadCardHtml(job, { variant: 'panel' })).join('');
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

  async function refresh({ discover = false } = {}) {
    try {
      const data = await api(discover ? '/api/hf/downloads?discover=1' : '/api/hf/downloads');
      const incoming = data.jobs || [];
      const seen = new Set();
      incoming.forEach((job) => {
        jobs.set(job.id, job);
        seen.add(job.id);
      });
      for (const id of [...jobs.keys()]) {
        if (seen.has(id)) continue;
        if (jobs.get(id)?.status === 'downloading') continue;
        jobs.delete(id);
      }
      emit();
      if (activeJobs().length) ensurePolling();
      else stopPollingIfIdle();
    } catch {
      stopPollingIfIdle();
    }
  }

  function track(meta, { navigate = false } = {}) {
    const jobId = String(meta?.jobId || meta?.job_id || '').trim();
    if (!jobId) return;
    if (meta?.label) labels.set(jobId, String(meta.label));
    if (meta?.meta && typeof meta.meta === 'object') {
      metaById.set(jobId, { ...meta.meta });
    }
    jobs.set(jobId, {
      id: jobId,
      repo_id: meta?.repoId || meta?.repo_id || '',
      filename: meta?.filename || '',
      status: 'downloading',
      progress: null,
      started_at: Date.now() / 1000,
      path: meta?.path || '',
    });
    ensurePolling();
    void refresh();
    if (navigate) openPanel();
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

  function openDownloadsView() {
    closePanel();
    window.DFlashDownloadsLive?.showPane?.('active');
    window.DFlashShell?.setView?.('downloads');
  }

  function openPanel() {
    openDownloadsView();
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
      openDownloadsView();
    });
    document.addEventListener('contextmenu', handleDownloadCardContextMenu);
    document.addEventListener('click', hideDownloadContextMenu);
    document.addEventListener('scroll', hideDownloadContextMenu, true);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closePanel();
        hideDownloadContextMenu();
      }
    });
    document.addEventListener('click', (event) => {
      const resumeBtn = event.target.closest('[data-resume-job]');
      if (resumeBtn) {
        event.preventDefault();
        event.stopPropagation();
        void resumeDownloadJob(resumeBtn.dataset.resumeJob);
        return;
      }
      if (!panelOpen) return;
      if (trayEl()?.contains(event.target)) return;
      closePanel();
    });
    void refresh({ discover: true });
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
    resumeDownloadJob,
    openPanel,
    closePanel,
    progressLabel,
    progressWidth,
    formatBytes,
    formatSpeed,
    formatEta,
    formatElapsed,
    renderDownloadCardHtml,
    getJobMeta,
  };
})();
