/** Model Search modal — live Hugging Face catalog */
(function () {
  const { api, toast } = window.ConsoleApi;

  let models = [];
  let selectedId = '';
  let selectedDetail = null;
  let searchTimer = null;
  let downloadPoll = null;
  let modelLibraries = [];
  let downloadLibraryId = '';

  const CATEGORY_LABELS = {
    dflash: 'DFlash / speculative',
    'text-generation': 'Text generation',
    'all-gguf': 'All GGUF',
    'text-to-speech': 'Text-to-speech',
    'automatic-speech-recognition': 'Speech-to-text',
    'image-to-text': 'OCR / image-to-text',
    'feature-extraction': 'Embeddings',
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function authorAvatarUrl(author) {
    const name = String(author || '').trim();
    if (!name) return '';
    return `https://huggingface.co/${encodeURIComponent(name)}/avatar`;
  }

  function avatarImg(author, avatarUrl, className) {
    const src = avatarUrl || authorAvatarUrl(author);
    if (!src) return `<span class="${className} is-fallback" aria-hidden="true"></span>`;
    return `<img class="${className}" src="${escapeHtml(src)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'${className} is-fallback',ariaHidden:'true'}))">`;
  }

  function stripFrontmatter(text) {
    return String(text || '').replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
  }

  function stripDuplicateTitle(text, modelId) {
    let body = String(text || '');
    if (!modelId) return body;
    const slug = String(modelId).split('/').pop().toLowerCase().replace(/[^a-z0-9]+/g, '');
    const lines = body.split('\n');
    if (!lines.length) return body;
    const first = lines[0].trim();
    if (/^#\s+/.test(first)) {
      const titleSlug = first.replace(/^#\s+/, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
      if (!titleSlug || titleSlug.includes(slug) || slug.includes(titleSlug) || titleSlug.length <= 48) {
        body = lines.slice(1).join('\n').replace(/^\s+/, '');
      }
    }
    return body.replace(/^!\[[^\]]*\]\([^)]+\)\s*\n+/i, '');
  }

  function sanitizeReadmeHtml(html) {
    if (window.DOMPurify) {
      return window.DOMPurify.sanitize(html, {
        ADD_ATTR: ['target', 'rel', 'loading'],
        ALLOWED_URI_REGEXP: /^(?:(?:https?|data):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
      });
    }
    const doc = new DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll('script, iframe, object, embed, form, input, button, style, link, meta').forEach((el) => {
      el.remove();
    });
    doc.querySelectorAll('*').forEach((el) => {
      [...el.attributes].forEach((attr) => {
        if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
      });
    });
    return doc.body.innerHTML;
  }

  function fixReadmeImages(container) {
    container?.querySelectorAll('img').forEach((img) => {
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      img.onerror = () => {
        img.classList.add('is-broken');
        img.alt = '';
      };
    });
  }

  function renderMarkdownFallback(source) {
    let text = stripFrontmatter(source);
    const codeBlocks = [];
    text = text.replace(/```([\s\S]*?)```/g, (_, code) => {
      const index = codeBlocks.length;
      codeBlocks.push(`<pre><code>${escapeHtml(code.trim())}</code></pre>`);
      return `\x00CODE${index}\x00`;
    });
    text = escapeHtml(text);
    text = text
      .replace(/^###### (.+)$/gm, '<h6>$1</h6>')
      .replace(/^##### (.+)$/gm, '<h5>$1</h5>')
      .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      .replace(/^# (.+)$/gm, '<h1>$1</h1>')
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img alt="$1" src="$2" loading="lazy">')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/^\s*[-*] (.+)$/gm, '<li>$1</li>')
      .replace(/(<li>[\s\S]*?<\/li>)/g, (block) => `<ul>${block}</ul>`)
      .replace(/\n{2,}/g, '</p><p>')
      .replace(/\n/g, '<br>');
    codeBlocks.forEach((block, index) => {
      text = text.replace(`\x00CODE${index}\x00`, block);
    });
    if (!/^<\s*(h[1-6]|p|ul|pre|blockquote|div|img)/i.test(text.trim())) {
      text = `<p>${text}</p>`;
    }
    return text;
  }

  function preprocessReadme(text) {
    return String(text || '')
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, src) => {
        if (/img\.shields\.io|badge|shields\.io/i.test(src)) {
          return `<img class="lm-readme-badge" alt="${alt.replace(/"/g, '')}" src="${src}" loading="lazy">`;
        }
        return match;
      });
  }

  function renderReadmeContent(raw, modelId) {
    let text = preprocessReadme(stripDuplicateTitle(stripFrontmatter(raw || ''), modelId));
    if (!text.trim()) return '<p class="lm-readme-empty">README not available.</p>';

    const htmlTags = (text.match(/<\s*[a-z][^>]*>/gi) || []).length;
    let html = '';
    if (htmlTags >= 2) {
      html = sanitizeReadmeHtml(text);
    } else if (window.marked?.parse) {
      html = sanitizeReadmeHtml(window.marked.parse(text, { gfm: true, breaks: false }));
    } else {
      html = renderMarkdownFallback(text);
    }
    const wrap = document.createElement('div');
    wrap.className = 'lm-readme-rendered lm-readme-md';
    wrap.innerHTML = html;
    fixReadmeImages(wrap);
    return wrap.outerHTML;
  }

  function currentCategory() {
    return document.getElementById('hfSearchCategory')?.value || 'dflash';
  }

  function categoryLabel(category) {
    return CATEGORY_LABELS[category] || 'Models';
  }

  function searchInput() {
    return document.getElementById('hfSearchInput');
  }

  function searchList() {
    return document.getElementById('hfSearchList');
  }

  function detailPane() {
    return document.getElementById('hfSearchDetail');
  }

  async function loadLibraries() {
    try {
      const data = await api('/api/hardware');
      modelLibraries = (data.model_libraries || []).filter((row) => row.enabled !== false);
      downloadLibraryId = data.download_library_id || modelLibraries[0]?.id || '';
      if (!modelLibraries.length && data.models_dir) {
        modelLibraries = [{ id: 'default', label: 'DFlash checkpoints', path: data.models_dir, enabled: true }];
        downloadLibraryId = 'default';
      }
    } catch {
      modelLibraries = [];
      downloadLibraryId = '';
    }
  }

  function libraryOptions(selectedId) {
    if (!modelLibraries.length) {
      return '<option value="">Default checkpoints folder</option>';
    }
    return modelLibraries.map((row) =>
      `<option value="${escapeHtml(row.id)}"${row.id === selectedId ? ' selected' : ''}>${escapeHtml(row.label || row.id)}</option>`,
    ).join('');
  }

  function downloadTargetLabel(libraryId) {
    const row = modelLibraries.find((item) => item.id === libraryId);
    return row?.path || row?.label || 'DFlash checkpoints folder';
  }

  function renderListLoading(message) {
    const list = searchList();
    if (list) list.innerHTML = `<div class="lm-search-empty">${escapeHtml(message || 'Searching…')}</div>`;
  }

  function renderList() {
    const list = searchList();
    const header = document.getElementById('hfSearchHeader');
    const query = searchInput()?.value?.trim() || '';
    const category = currentCategory();
    if (header) {
      header.textContent = query
        ? `Results for “${query}”`
        : `Popular ${categoryLabel(category).toLowerCase()} models`;
    }
    if (!models.length) {
      list.innerHTML = `<div class="lm-search-empty">No models found for ${escapeHtml(categoryLabel(category))}. Try another search or category.</div>`;
      return;
    }
    list.innerHTML = models.map((model) => {
      const selected = model.id === selectedId ? ' selected' : '';
      return `
        <button type="button" class="lm-search-item${selected}" data-repo-id="${escapeHtml(model.id)}">
          ${avatarImg(model.author, model.author_avatar_url, 'lm-hf-avatar sm')}
          <div class="lm-search-item-main">
            <span class="lm-search-item-name">${escapeHtml(model.label || model.id)}</span>
            <span class="lm-search-item-desc">${escapeHtml(model.author || '—')} · ${escapeHtml(model.downloads_label || '0')} downloads</span>
          </div>
          <span class="lm-search-item-meta">${escapeHtml(model.updated_ago || '—')}</span>
        </button>`;
    }).join('');

    list.querySelectorAll('.lm-search-item').forEach((btn) => {
      btn.addEventListener('click', () => {
        void selectModel(btn.dataset.repoId);
      });
    });
  }

  function metaTags(model) {
    const tags = Array.isArray(model.tags) ? model.tags : [];
    const picked = [];
    const params = tags.find((t) => /\d+b/i.test(t));
    if (params) picked.push(`PARAMS ${params.toUpperCase()}`);
    const arch = tags.find((t) => /llama|gemma|qwen|mistral|phi|gpt/i.test(t));
    if (arch) picked.push(`ARCH ${arch}`);
    const files = model.download_files || model.gguf_files || [];
    const format = files[0]?.format || (model.has_gguf ? 'gguf' : 'file');
    picked.push(`FORMAT ${String(format).toUpperCase()}`);
    if (model.pipeline_tag) picked.push(model.pipeline_tag.toUpperCase());
    return picked.slice(0, 5).map((t) => `<span>${escapeHtml(t)}</span>`).join('');
  }

  function renderDetail(model) {
    const pane = detailPane();
    if (!pane || !model) return;
    const files = Array.isArray(model.download_files) ? model.download_files
      : (Array.isArray(model.gguf_files) ? model.gguf_files : []);
    const fileOptions = files.map((file, idx) =>
      `<option value="${escapeHtml(file.filename)}"${idx === 0 ? ' selected' : ''}>${escapeHtml(file.label)}${file.size_gb != null ? ` · ${file.size_gb} GB` : ''}</option>`,
    ).join('');
    const libraryPick = modelLibraries.length > 1
      ? `<select class="lm-select" id="hfLibraryPick">${libraryOptions(downloadLibraryId)}</select>`
      : '';
    const downloadRow = files.length
      ? `<div class="lm-search-download-row">
          <select class="lm-select" id="hfFilePick">${fileOptions}</select>
          ${libraryPick}
          <button class="lm-btn hf-primary" type="button" id="hfDownloadBtn">Download</button>
        </div>`
      : '';
    const savePath = downloadTargetLabel(downloadLibraryId);
    const downloadNote = files.length
      ? `<p class="lm-gpu-ok lm-search-save-path">Saved to <code>${escapeHtml(savePath)}</code></p>`
      : '<p class="lm-setting-desc">No downloadable files listed on Hugging Face for this repo.</p>';
    const downloadStatus = files.length
      ? '<p class="lm-search-download-status hidden" id="hfDownloadStatus"></p>'
      : '';

    pane.innerHTML = `
      <div class="lm-search-detail-head">
        ${avatarImg(model.author, model.author_avatar_url, 'lm-hf-avatar')}
        <div class="lm-search-detail-identity">
          <div class="lm-search-detail-title-row">
            <h2 title="${escapeHtml(model.id)}">${escapeHtml(model.label || model.id.split('/').pop() || model.id)}</h2>
            <div class="lm-search-detail-tools">
              <button class="lm-icon-btn tiny" type="button" id="hfCopyRepo" title="Copy repo id">⧉</button>
              <a class="lm-btn ghost small" href="${escapeHtml(model.url)}" target="_blank" rel="noopener noreferrer">Open HF</a>
              <button class="lm-icon-btn" type="button" data-action="close-modal" aria-label="Close">✕</button>
            </div>
          </div>
          <p class="lm-search-stats">${escapeHtml(model.downloads_label || '0')} downloads · ★ ${model.likes || 0} · Updated ${escapeHtml(model.updated_ago || '—')}</p>
          <p class="lm-search-repo-id">${escapeHtml(model.id)}</p>
        </div>
        ${downloadRow}
      </div>
      <p class="lm-search-description">${escapeHtml(model.description || 'No description on Hugging Face.')}</p>
      <div class="lm-meta-tags">${metaTags(model)}</div>
      ${downloadNote}
      ${downloadStatus}
      <section class="lm-readme">
        <h3>README</h3>
        <div class="lm-readme-body">${renderReadmeContent(model.readme, model.id)}</div>
      </section>`;

    document.getElementById('hfCopyRepo')?.addEventListener('click', () => {
      navigator.clipboard.writeText(model.id).then(() => toast('Repo id copied'));
    });
    document.getElementById('hfLibraryPick')?.addEventListener('change', (e) => {
      downloadLibraryId = e.target.value;
      const note = pane.querySelector('.lm-search-save-path code');
      if (note) note.textContent = downloadTargetLabel(downloadLibraryId);
    });
    document.getElementById('hfDownloadBtn')?.addEventListener('click', () => {
      const pick = document.getElementById('hfFilePick');
      const filename = pick?.value;
      const libraryId = document.getElementById('hfLibraryPick')?.value || downloadLibraryId;
      if (filename) void startDownload(model.id, filename, libraryId);
    });
  }

  function renderDetailPlaceholder(message) {
    const pane = detailPane();
    if (!pane) return;
    pane.innerHTML = `<div class="lm-search-placeholder"><p>${escapeHtml(message || 'Select a model to view details.')}</p></div>`;
  }

  async function runSearch() {
    const query = searchInput()?.value?.trim() || '';
    const sort = document.getElementById('hfSearchSort')?.value || 'downloads';
    const category = currentCategory();
    renderListLoading('Searching Hugging Face…');
    try {
      const data = await api(`/api/hf/search?q=${encodeURIComponent(query)}&sort=${encodeURIComponent(sort)}&category=${encodeURIComponent(category)}&limit=25`);
      models = data.models || [];
      if (!models.some((m) => m.id === selectedId)) {
        selectedId = '';
        selectedDetail = null;
        renderDetailPlaceholder();
      }
      renderList();
      if (!selectedId && models[0]) await selectModel(models[0].id);
    } catch (err) {
      models = [];
      const message = /not found|404/i.test(err.message)
        ? 'Hugging Face search is unavailable. Restart DFlash Console, then try again.'
        : err.message;
      renderListLoading(message);
      renderDetailPlaceholder(message);
    }
  }

  function scheduleSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      searchTimer = null;
      void runSearch();
    }, 350);
  }

  async function selectModel(repoId) {
    if (!repoId) return;
    selectedId = repoId;
    renderList();
    renderDetailPlaceholder('Loading model details…');
    try {
      const category = currentCategory();
      const data = await api(`/api/hf/models/${encodeURIComponent(repoId)}?category=${encodeURIComponent(category)}`);
      selectedDetail = data.model;
      renderDetail(selectedDetail);
    } catch (err) {
      renderDetailPlaceholder(err.message);
      toast(err.message, false);
    }
  }

  async function pollDownload(jobId, repoId, filename) {
    if (downloadPoll) clearInterval(downloadPoll);
    const statusEl = document.getElementById('hfDownloadStatus');
    const showStatus = (text) => {
      if (!statusEl) return;
      statusEl.textContent = text;
      statusEl.classList.remove('hidden');
    };
    downloadPoll = window.setInterval(async () => {
      try {
        const data = await api(`/api/hf/download/${encodeURIComponent(jobId)}`);
        const job = data.job || {};
        if (job.status === 'downloading') {
          const pct = job.progress != null ? ` · ${Math.round(job.progress)}%` : '';
          const label = `Downloading ${filename}${pct}`;
          showStatus(label);
          window.DFlashStatusFeed?.setTransient(label, { secondary: repoId, ttlMs: 15000 });
        } else if (job.status === 'done') {
          clearInterval(downloadPoll);
          downloadPoll = null;
          showStatus(`Saved to ${job.path}`);
          toast('Download complete');
          window.DFlashStatusFeed?.note(`Downloaded ${filename}`, repoId);
          if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
        } else if (job.status === 'error') {
          clearInterval(downloadPoll);
          downloadPoll = null;
          showStatus(job.error || 'Download failed');
          toast(job.error || 'Download failed', false);
        }
      } catch (err) {
        clearInterval(downloadPoll);
        downloadPoll = null;
        showStatus(err.message);
        toast(err.message, false);
      }
    }, 1200);
  }

  async function startDownload(repoId, filename, libraryId) {
    window.DFlashStatusFeed?.setTransient('Starting download…', { secondary: filename, ttlMs: 30000 });
    try {
      const body = { repo_id: repoId, filename };
      if (libraryId) body.library_id = libraryId;
      const data = await api('/api/hf/download', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      toast('Download started');
      await pollDownload(data.job_id, repoId, filename);
    } catch (err) {
      toast(err.message, false);
    }
  }

  function setupSearchResize() {
    const layout = document.getElementById('hfSearchLayout');
    const left = document.getElementById('hfSearchLeft');
    const handle = document.getElementById('hfSearchSplitHandle');
    const dialog = document.querySelector('.lm-search-dialog');
    if (!layout || !left || !handle || !dialog) return;

    const widthMin = 180;
    const widthMax = () => clamp(Math.floor(dialog.getBoundingClientRect().width * 0.55), widthMin, 520);
    const storedWidth = parseInt(localStorage.getItem('dflashConsole.hfSearchLeftWidth') || '', 10);
    if (Number.isFinite(storedWidth) && storedWidth >= widthMin) {
      dialog.style.setProperty('--hf-search-left-width', `${storedWidth}px`);
    }

    const startResize = (clientX) => {
      const startX = clientX;
      const startW = left.getBoundingClientRect().width;
      document.body.classList.add('lm-resizing-hf-search');

      const onMove = (ev) => {
        const next = clamp(startW + (ev.clientX - startX), widthMin, widthMax());
        dialog.style.setProperty('--hf-search-left-width', `${next}px`);
      };

      const onUp = () => {
        document.body.classList.remove('lm-resizing-hf-search');
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        const width = Math.round(left.getBoundingClientRect().width);
        localStorage.setItem('dflashConsole.hfSearchLeftWidth', String(width));
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    };

    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startResize(e.clientX);
    });

    handle.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const current = left.getBoundingClientRect().width;
      const delta = e.key === 'ArrowRight' ? 16 : -16;
      const next = clamp(current + delta, widthMin, widthMax());
      dialog.style.setProperty('--hf-search-left-width', `${next}px`);
      localStorage.setItem('dflashConsole.hfSearchLeftWidth', String(next));
    });
  }

  async function onModalOpen() {
    await loadLibraries();
    const input = searchInput();
    if (input) {
      input.focus();
      void runSearch();
    }
  }

  function bind() {
    searchInput()?.addEventListener('input', scheduleSearch);
    document.getElementById('hfSearchSort')?.addEventListener('change', () => void runSearch());
    document.getElementById('hfSearchCategory')?.addEventListener('change', () => void runSearch());
    setupSearchResize();

    const modal = document.getElementById('modelSearchModal');
    document.querySelector('[data-action="open-model-search"]')?.addEventListener('click', () => {
      window.setTimeout(onModalOpen, 0);
    });
    if (modal) {
      const observer = new MutationObserver(() => {
        if (modal.classList.contains('open')) void onModalOpen();
      });
      observer.observe(modal, { attributes: true, attributeFilter: ['class'] });
    }
  }

  document.addEventListener('DOMContentLoaded', bind);
})();
