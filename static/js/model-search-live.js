/** Model Search modal — live Hugging Face catalog */
(function () {
  const { api, toast } = window.ConsoleApi;

  let models = [];
  let selectedId = '';
  let selectedDetail = null;
  let searchTimer = null;
  let modelLibraries = [];
  let downloadLibraryId = '';
  let queueUnsubscribe = null;
  const notifiedJobs = new Set();

  const CATEGORY_LABELS = {
    dflash: 'DFlash / speculative',
    'text-generation': 'Text generation',
    'all-gguf': 'All GGUF',
    'text-to-speech': 'Text-to-speech',
    'automatic-speech-recognition': 'Speech-to-text',
    'image-to-text': 'OCR / image-to-text',
    'feature-extraction': 'Embeddings',
  };

  const COMMON_LABS = [
    'Google',
    'Qwen',
    'Meta',
    'Mistral AI',
    'Microsoft',
    'DeepSeek',
    'z-lab',
    'LM Studio',
    'NVIDIA',
    'IBM',
    'Apple',
    'Cohere',
    'OpenAI',
    'Anthropic',
    'BAAI',
    'Alibaba',
  ];

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

  function authorInitial(author) {
    const name = String(author || '').trim();
    return (name.charAt(0) || '?').toUpperCase();
  }

  function avatarFallbackMarkup(className, author) {
    return `<span class="${className} is-fallback" aria-hidden="true">${escapeHtml(authorInitial(author))}</span>`;
  }

  function avatarImg(author, avatarUrl, className) {
    const src = avatarUrl || authorAvatarUrl(author);
    if (!src) return avatarFallbackMarkup(className, author);
    const fallback = avatarFallbackMarkup(className, author).replace(/"/g, '&quot;');
    return `<img class="${className}" src="${escapeHtml(src)}" alt="" loading="lazy" onerror="this.outerHTML='${fallback}'">`;
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

  function currentCreator() {
    return document.getElementById('hfSearchCreator')?.value || '';
  }

  function modelLab(model) {
    return model.lab || model.author || '—';
  }

  function visibleModels() {
    const lab = currentCreator();
    if (!lab) return models;
    return models.filter((model) => modelLab(model) === lab);
  }

  function populateCreatorFilter() {
    const select = document.getElementById('hfSearchCreator');
    if (!select) return;
    const previous = select.value;
    const resultLabs = [...new Set(models.map((model) => modelLab(model)).filter(Boolean))];
    const labs = [...new Set([...COMMON_LABS, ...resultLabs])]
      .sort((a, b) => a.localeCompare(b));
    select.innerHTML = [
      '<option value="">All labs</option>',
      ...labs.map((lab) => `<option value="${escapeHtml(lab)}">${escapeHtml(lab)}</option>`),
    ].join('');
    select.value = previous && labs.includes(previous) ? previous : '';
    window.DFlashSelectTheme?.enhanceAll?.(select.closest('.df-catalog-toolbar'));
  }

  function listSizeLabel(model) {
    if (model.size_label && model.size_label !== '—') return model.size_label;
    if (model.size_gb != null) return `${model.size_gb} GB`;
    return '—';
  }

  function listAgeLabel(model) {
    if (model.updated_days != null) return `${model.updated_days} days`;
    if (model.updated_ago && model.updated_ago !== '—') return model.updated_ago;
    return '—';
  }

  function modelTitle(model) {
    return model.title || model.label || model.id || '—';
  }

  function modelDescription(model) {
    return String(model.description || '').trim();
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
        modelLibraries = [{ id: 'default', label: 'DFlash models', path: data.models_dir, enabled: true }];
        downloadLibraryId = 'default';
      }
    } catch {
      modelLibraries = [];
      downloadLibraryId = '';
    }
  }

  function libraryOptions(selectedId) {
    if (!modelLibraries.length) {
      return '<option value="">Default models folder</option>';
    }
    return modelLibraries.map((row) =>
      `<option value="${escapeHtml(row.id)}"${row.id === selectedId ? ' selected' : ''}>${escapeHtml(row.label || row.id)}</option>`,
    ).join('');
  }

  function downloadTargetLabel(libraryId) {
    const row = modelLibraries.find((item) => item.id === libraryId);
    return row?.path || row?.label || 'DFlash models folder';
  }

  function renderListLoading(message) {
    const list = searchList();
    if (list) list.innerHTML = `<div class="lm-search-empty">${escapeHtml(message || 'Searching…')}</div>`;
  }

  function renderList() {
    const list = searchList();
    const category = currentCategory();
    const visible = visibleModels();
    if (!visible.length) {
      const creator = currentCreator();
      const hint = creator
        ? `No ${creator} models in this search. Try another lab or clear the filter.`
        : `No models found for ${escapeHtml(categoryLabel(category))}. Try another search or category.`;
      list.innerHTML = `<div class="lm-search-empty">${escapeHtml(hint)}</div>`;
      return;
    }
    list.innerHTML = visible.map((model) => {
      const selected = model.id === selectedId ? ' selected' : '';
      const description = modelDescription(model);
      const labName = modelLab(model);
      const descLine = description
        ? `<span class="lm-search-item-summary">${escapeHtml(description)}</span>`
        : `<span class="lm-search-item-desc">
            <span class="lm-search-item-author">${escapeHtml(labName)}</span>
            · ${escapeHtml(model.downloads_label || '0')} downloads
          </span>`;
      const metaLine = description
        ? `<span class="lm-search-item-desc">
            <span class="lm-search-item-author">${escapeHtml(labName)}</span>
            · ${escapeHtml(model.downloads_label || '0')} downloads
          </span>`
        : '';
      return `
        <button type="button" class="lm-search-item${selected}" data-repo-id="${escapeHtml(model.id)}">
          ${avatarImg(model.author, model.author_avatar_url, 'lm-hf-avatar sm')}
          <div class="lm-search-item-main">
            <span class="lm-search-item-name">${escapeHtml(modelTitle(model))}</span>
            ${descLine}
            ${metaLine}
          </div>
          <div class="lm-search-item-stats">
            <span class="lm-search-item-stat">${escapeHtml(listSizeLabel(model))}</span>
            <span class="lm-search-item-stat">${escapeHtml(listAgeLabel(model))}</span>
          </div>
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

  function getSelectedFilename() {
    const pick = document.getElementById('hfFilePick');
    return pick?.value || pick?.getAttribute('value') || '';
  }

  function localInstallFromModel(model, filename) {
    const map = model?.local_installs || {};
    const rows = map[filename] || [];
    return rows[0] || null;
  }

  async function resolveLocalInstall(model, filename) {
    if (!model?.id || !filename) return null;
    let install = localInstallFromModel(model, filename);
    if (install) return install;
    try {
      const data = await api(
        `/api/hf/local-match?repo_id=${encodeURIComponent(model.id)}&filename=${encodeURIComponent(filename)}`,
      );
      if (!model.local_installs) model.local_installs = {};
      model.local_installs[filename] = data.matches || [];
      return (data.matches || [])[0] || null;
    } catch {
      return null;
    }
  }

  async function refreshInstallUI(model) {
    if (!model) return;
    const filename = getSelectedFilename();
    const activeJob = window.DFlashDownloadQueue?.getActiveJob?.(model.id, filename);
    if (activeJob) {
      updateCardDownloadUI(model, activeJob);
      return;
    }
    const install = filename ? await resolveLocalInstall(model, filename) : null;
    const btn = document.getElementById('hfDownloadBtn');
    const saveNote = document.getElementById('hfSaveNote');
    const installedNote = document.getElementById('hfInstalledNote');
    hideCardDownloadProgress();
    if (install) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Load model';
        btn.dataset.action = 'load';
      }
      saveNote?.classList.add('hidden');
      if (installedNote) {
        installedNote.classList.remove('hidden');
        installedNote.innerHTML = `
          <span class="df-catalog-installed-label">Already installed</span>
          <span class="df-catalog-installed-library">${escapeHtml(install.library_label || 'Local library')}</span>
          <code class="df-catalog-installed-path">${escapeHtml(install.path || '')}</code>`;
      }
    } else {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Download';
        btn.dataset.action = 'download';
      }
      saveNote?.classList.remove('hidden');
      installedNote?.classList.add('hidden');
      if (installedNote) installedNote.innerHTML = '';
    }
    const statusEl = document.getElementById('hfDownloadStatus');
    statusEl?.classList.add('hidden');
    window.DFlashSelectTheme?.enhanceAll?.(document.getElementById('hfSearchDetail'));
  }

  function hideCardDownloadProgress() {
    document.getElementById('hfDownloadProgress')?.classList.add('hidden');
  }

  function updateCardDownloadUI(model, job) {
    if (!model || !job) return;
    const queue = window.DFlashDownloadQueue;
    const progressEl = document.getElementById('hfDownloadProgress');
    const labelEl = document.getElementById('hfDownloadProgressLabel');
    const pctEl = document.getElementById('hfDownloadProgressPct');
    const fillEl = document.getElementById('hfDownloadProgressFill');
    const statusEl = document.getElementById('hfDownloadStatus');
    const btn = document.getElementById('hfDownloadBtn');
    const saveNote = document.getElementById('hfSaveNote');
    const installedNote = document.getElementById('hfInstalledNote');

    const pctLabel = queue?.progressLabel?.(job) || 'Downloading…';
    const width = queue?.progressWidth?.(job);
    const indeterminate = job.status === 'downloading' && width == null;

    progressEl?.classList.remove('hidden');
    if (labelEl) {
      labelEl.textContent = `Downloading ${job.filename || modelTitle(model)}`;
    }
    if (pctEl) pctEl.textContent = pctLabel;
    if (fillEl) {
      fillEl.classList.toggle('is-indeterminate', indeterminate);
      fillEl.style.width = width != null ? `${width}%` : '';
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Downloading…';
      btn.dataset.action = 'downloading';
    }
    saveNote?.classList.add('hidden');
    installedNote?.classList.add('hidden');

    if (statusEl) {
      const bytes = job.bytes_total
        ? `${queue?.formatBytes?.(job.bytes_read) || ''} / ${queue?.formatBytes?.(job.bytes_total) || ''}`
        : '';
      statusEl.textContent = bytes ? `Downloading to ${job.path || 'models folder'} · ${bytes}` : `Downloading to ${job.path || 'models folder'}`;
      statusEl.classList.remove('hidden');
    }

    if (job.status === 'done') {
      hideCardDownloadProgress();
      if (statusEl) {
        statusEl.textContent = `Saved to ${job.path || 'models folder'}`;
        statusEl.classList.remove('hidden');
      }
      if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
      void refreshInstallUI(model);
    } else if (job.status === 'error') {
      hideCardDownloadProgress();
      if (statusEl) {
        statusEl.textContent = job.error || 'Download failed';
        statusEl.classList.remove('hidden');
      }
      void refreshInstallUI(model);
    }
  }

  function handleDownloadQueueUpdate(jobs) {
    if (!selectedDetail) return;
    const filename = getSelectedFilename();
    const job = (jobs || []).find((row) =>
      row.repo_id === selectedDetail.id && row.filename === filename,
    );
    if (!job) {
      if (!window.DFlashDownloadQueue?.getActiveJob?.(selectedDetail.id, filename)) {
        void refreshInstallUI(selectedDetail);
      }
      return;
    }
    if (job.status === 'downloading') {
      updateCardDownloadUI(selectedDetail, job);
      return;
    }
    if ((job.status === 'done' || job.status === 'error') && !notifiedJobs.has(job.id)) {
      notifiedJobs.add(job.id);
      if (job.status === 'done') {
        toast('Download complete');
        window.DFlashStatusFeed?.note(`Downloaded ${job.filename || 'model'}`, selectedDetail.id);
      } else {
        toast(job.error || 'Download failed', false);
      }
      updateCardDownloadUI(selectedDetail, job);
    }
  }

  function openInstalledModel(install) {
    if (!install?.path) {
      toast('Local model path not found', false);
      return;
    }
    localStorage.setItem('dflashConsole.chatCheckpointKey', install.path);
    window.DFlashShell?.setView('chat');
    void window.DFlashChatLive?.onViewEnter?.();
    toast('Selected in Playground — pick an engine and click Load');
  }

  function renderDetail(model) {
    const pane = detailPane();
    if (!pane || !model) return;
    const files = Array.isArray(model.download_files) ? model.download_files
      : (Array.isArray(model.gguf_files) ? model.gguf_files : []);
    const fileOptions = files.map((file, idx) =>
      `<option value="${escapeHtml(file.filename)}"${idx === 0 ? ' selected' : ''}>${escapeHtml(file.label)}${file.size_gb != null ? ` · ${file.size_gb} GB` : ''}</option>`,
    ).join('');
    const filePick = files.length > 1
      ? `<div class="df-catalog-file-row">
          <label class="df-catalog-field-label" for="hfFilePick">Quantization</label>
          <select class="lm-select small" id="hfFilePick">${fileOptions}</select>
        </div>`
      : (files.length === 1
        ? `<input type="hidden" id="hfFilePick" value="${escapeHtml(files[0].filename)}">`
        : '');
    const downloadBtn = files.length
      ? '<button class="lm-btn hf-primary small" type="button" id="hfDownloadBtn" data-action="download">Download</button>'
      : '';
    const savePath = downloadTargetLabel(downloadLibraryId);
    const downloadNote = files.length
      ? `<p class="lm-gpu-ok lm-search-save-path" id="hfSaveNote">New downloads save to <code>${escapeHtml(savePath)}</code></p>
         <div class="df-catalog-installed-note hidden" id="hfInstalledNote"></div>`
      : '<p class="lm-setting-desc">No downloadable files listed on Hugging Face for this repo.</p>';
    const downloadStatus = files.length
      ? '<p class="lm-search-download-status hidden" id="hfDownloadStatus"></p>'
      : '';

    const summaryText = modelDescription(model) || 'No description on Hugging Face.';
    pane.innerHTML = `
      <div class="df-catalog-model-card">
        <div class="df-catalog-download-progress hidden" id="hfDownloadProgress">
          <div class="df-catalog-download-progress-head">
            <span id="hfDownloadProgressLabel">Downloading…</span>
            <span id="hfDownloadProgressPct">0%</span>
          </div>
          <div class="df-catalog-download-progress-bar">
            <div class="df-catalog-download-progress-fill" id="hfDownloadProgressFill"></div>
          </div>
        </div>
        <div class="lm-search-detail-head">
          <div class="lm-search-detail-top">
            ${avatarImg(model.author, model.author_avatar_url, 'lm-hf-avatar')}
            <div class="lm-search-detail-identity">
              <div class="lm-search-detail-title-row">
                <h2 title="${escapeHtml(model.id)}">${escapeHtml(modelTitle(model))}</h2>
                <div class="lm-search-detail-tools">
                  ${downloadBtn}
                  <button class="lm-icon-btn tiny" type="button" id="hfCopyRepo" title="Copy repo id">⧉</button>
                  <a class="lm-btn ghost small" href="${escapeHtml(model.url)}" target="_blank" rel="noopener noreferrer">Open HF</a>
                </div>
              </div>
              <p class="lm-search-stats">
                <span class="lm-search-item-author">${escapeHtml(modelLab(model))}</span>
                · ${escapeHtml(model.author || '—')}
                · ${escapeHtml(model.downloads_label || '0')} downloads · ★ ${model.likes || 0}
                · Updated ${escapeHtml(model.updated_ago || '—')}${model.size_label && model.size_label !== '—' ? ` · ${escapeHtml(model.size_label)}` : ''}
              </p>
              <p class="lm-search-repo-id">${escapeHtml(model.id)}</p>
              <p class="lm-search-description">${escapeHtml(summaryText)}</p>
              ${filePick}
            </div>
          </div>
        </div>
        <div class="lm-meta-tags">${metaTags(model)}</div>
        ${downloadNote}
        ${downloadStatus}
        <section class="lm-readme">
          <h3>README</h3>
          <div class="lm-readme-body">${renderReadmeContent(model.readme, model.id)}</div>
        </section>
      </div>`;

    document.getElementById('hfCopyRepo')?.addEventListener('click', () => {
      navigator.clipboard.writeText(model.id).then(() => toast('Repo id copied'));
    });
    document.getElementById('hfFilePick')?.addEventListener('change', () => {
      void refreshInstallUI(model);
    });
    document.getElementById('hfDownloadBtn')?.addEventListener('click', async () => {
      const btn = document.getElementById('hfDownloadBtn');
      const filename = getSelectedFilename();
      if (!filename || btn?.disabled || btn?.dataset.action === 'downloading') return;
      if (btn?.dataset.action === 'load') {
        const install = await resolveLocalInstall(model, filename);
        openInstalledModel(install);
        return;
      }
      const libraryId = document.getElementById('hfLibraryPick')?.value || downloadLibraryId;
      void startDownload(model.id, filename, libraryId, model);
    });
    void refreshInstallUI(model);
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
      populateCreatorFilter();
      if (!models.some((m) => m.id === selectedId)) {
        selectedId = '';
        selectedDetail = null;
        renderDetailPlaceholder();
      }
      renderList();
      const visible = visibleModels();
      if (!selectedId && visible[0]) await selectModel(visible[0].id);
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

  async function startDownload(repoId, filename, libraryId, model) {
    const install = model ? await resolveLocalInstall(model, filename) : null;
    if (install) {
      toast('Already installed on this PC — use Load model', false);
      void refreshInstallUI(model);
      return;
    }
    if (window.DFlashDownloadQueue?.getActiveJob?.(repoId, filename)) {
      toast('Download already in progress', false);
      void refreshInstallUI(model);
      return;
    }
    window.DFlashStatusFeed?.setTransient('Starting download…', { secondary: filename, ttlMs: 30000 });
    try {
      const body = { repo_id: repoId, filename };
      if (libraryId) body.library_id = libraryId;
      const resp = await fetch('/api/hf/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      let data = null;
      try {
        data = await resp.json();
      } catch {
        data = null;
      }
      if (!resp.ok) {
        const detail = data?.detail || data;
        if (detail?.already_installed || data?.already_installed) {
          const payload = detail?.already_installed ? detail : data;
          if (model && payload.matches) {
            if (!model.local_installs) model.local_installs = {};
            model.local_installs[filename] = payload.matches;
          }
          toast('Already installed on this PC — use Load model', false);
          void refreshInstallUI(model);
          return;
        }
        const message = typeof detail === 'string' ? detail : (detail?.error || `HTTP ${resp.status}`);
        throw new Error(message);
      }
      window.DFlashDownloadQueue?.track?.({
        jobId: data.job_id,
        repoId,
        filename,
        label: model ? modelTitle(model) : filename,
        path: data.path,
      });
      toast('Download started — see progress above');
      if (model) {
        updateCardDownloadUI(model, {
          id: data.job_id,
          repo_id: repoId,
          filename,
          status: 'downloading',
          progress: 0,
          path: data.path,
        });
      }
    } catch (err) {
      toast(err.message || 'Download failed', false);
    }
  }

  function setupSearchResize() {
    const layout = document.getElementById('hfSearchLayout');
    const left = document.getElementById('hfSearchLeft');
    const handle = document.getElementById('hfSearchSplitHandle');
    const container = document.querySelector('.lm-view[data-view="catalog"] .df-catalog-shell');
    if (!layout || !left || !handle || !container) return;

    const widthMin = 180;
    const widthMax = () => clamp(Math.floor(container.getBoundingClientRect().width * 0.55), widthMin, 520);
    const storedWidth = parseInt(localStorage.getItem('dflashConsole.hfSearchLeftWidth') || '', 10);
    if (Number.isFinite(storedWidth) && storedWidth >= widthMin) {
      container.style.setProperty('--hf-search-left-width', `${storedWidth}px`);
    }

    const startResize = (clientX) => {
      const startX = clientX;
      const startW = left.getBoundingClientRect().width;
      document.body.classList.add('lm-resizing-hf-search');

      const onMove = (ev) => {
        const next = clamp(startW + (ev.clientX - startX), widthMin, widthMax());
        container.style.setProperty('--hf-search-left-width', `${next}px`);
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
      container.style.setProperty('--hf-search-left-width', `${next}px`);
      localStorage.setItem('dflashConsole.hfSearchLeftWidth', String(next));
    });
  }

  async function onViewEnter() {
    await loadLibraries();
    const input = searchInput();
    if (input) {
      input.focus();
      void runSearch();
    }
  }

  function onCreatorFilterChange() {
    const visible = visibleModels();
    if (selectedId && !visible.some((model) => model.id === selectedId)) {
      selectedId = '';
      selectedDetail = null;
      if (visible[0]) void selectModel(visible[0].id);
      else renderDetailPlaceholder();
    }
    renderList();
  }

  function bind() {
    searchInput()?.addEventListener('input', scheduleSearch);
    document.getElementById('hfSearchSort')?.addEventListener('change', () => void runSearch());
    document.getElementById('hfSearchCategory')?.addEventListener('change', () => void runSearch());
    document.getElementById('hfSearchCreator')?.addEventListener('change', onCreatorFilterChange);
    setupSearchResize();
    if (queueUnsubscribe) queueUnsubscribe();
    queueUnsubscribe = window.DFlashDownloadQueue?.subscribe?.(handleDownloadQueueUpdate) || null;
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashModelSearchLive = { onViewEnter };
})();
