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
  const searchCache = new Map();
  const detailCache = new Map();
  const SEARCH_CACHE_STORAGE_KEY = 'dflashConsole.hfSearchCache';
  let searchRefreshGen = 0;
  let listDetailWarmGen = 0;
  let catalogPrimed = false;
  let listRefreshIndicator = null;
  let catalogContextModel = null;
  const CATALOG_REFRESH_MS = 10 * 60 * 1000;
  const LIST_DETAIL_WARM_WORKERS = 4;
  const listDetailPending = new Map();

  const DEFAULT_CATEGORY = 'supported';
  const CATEGORY_LABELS = {
    supported: 'Supported in Console',
    all: 'All models',
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

    // Always parse as Markdown (GFM). HF READMEs mix HTML + Markdown; skipping
    // marked when HTML tags are present left raw ## / tables / pipes on screen.
    let html = '';
    if (window.marked?.parse) {
      html = sanitizeReadmeHtml(window.marked.parse(text, { gfm: true, breaks: false }));
    } else {
      const looksLikeMarkdown = /(^|\n)\s{0,3}#{1,6}\s|(^|\n)\|.+\||```|\[[^\]]+\]\([^)]+\)/.test(text);
      html = looksLikeMarkdown
        ? sanitizeReadmeHtml(renderMarkdownFallback(text))
        : sanitizeReadmeHtml(text);
    }
    const wrap = document.createElement('div');
    wrap.className = 'lm-readme-rendered lm-readme-md';
    wrap.innerHTML = html;
    fixReadmeImages(wrap);
    return wrap.outerHTML;
  }

  function renderDescriptionHtml(text) {
    const raw = String(text || '').trim();
    if (!raw) return escapeHtml('No description on Hugging Face.');
    if (/<\s*[a-z]/i.test(raw)) return sanitizeReadmeHtml(raw);
    return escapeHtml(raw);
  }

  function formatCatalogFileSize(file) {
    if (!file) return '';
    const bytes = Number(file.size_bytes);
    if (Number.isFinite(bytes) && bytes > 0) {
      return window.DFlashDownloadQueue?.formatBytes?.(bytes) || `${file.size_gb} GB`;
    }
    if (file.size_gb != null && file.size_gb !== '') return `${file.size_gb} GB`;
    return '';
  }

  function selectedCatalogFile(files) {
    const list = Array.isArray(files) ? files : [];
    const filename = getSelectedFilename();
    if (!filename) return list[0] || null;
    return list.find((file) => file.filename === filename) || list[0] || null;
  }

  function updateSelectedFileSize(files) {
    const el = document.getElementById('hfSelectedFileSize');
    if (!el) return;
    const size = formatCatalogFileSize(selectedCatalogFile(files));
    el.textContent = size || '';
    el.classList.toggle('hidden', !size);
    el.title = size ? `File size on disk: ${size}` : '';
  }

  function currentCategory() {
    return document.getElementById('hfSearchCategory')?.value || DEFAULT_CATEGORY;
  }

  function applyCatalogDefaults() {
    const returnBar = document.getElementById('stackWizardCatalogReturn');
    if (returnBar && !returnBar.classList.contains('hidden')) return;
    const category = document.getElementById('hfSearchCategory');
    if (category) {
      category.value = DEFAULT_CATEGORY;
      window.DFlashSelectTheme?.syncSelect?.(category);
    }
  }

  function currentCreator() {
    return document.getElementById('hfSearchCreator')?.value || '';
  }

  function installedOnly() {
    return document.getElementById('hfSearchSort')?.value === 'installed';
  }

  function currentSort() {
    const value = document.getElementById('hfSearchSort')?.value || 'downloads';
    return value === 'installed' ? 'downloads' : value;
  }

  function modelLab(model) {
    return model.lab || model.author || '—';
  }

  function visibleModels() {
    let rows = models;
    if (installedOnly()) {
      rows = rows.filter((model) => catalogInstalled(model));
    }
    const lab = currentCreator();
    if (!lab) return rows;
    const needle = String(lab).trim().toLowerCase();
    return rows.filter((model) => String(modelLab(model)).trim().toLowerCase() === needle);
  }

  function populateCreatorFilter() {
    const select = document.getElementById('hfSearchCreator');
    if (!select) return;
    const previous = select.value;
    // Dedupe labs case-insensitively so "Microsoft" and "microsoft" collapse
    // into one option (the backend lab is the publisher, e.g. Microsoft).
    const labSet = new Map();
    const addLab = (lab) => {
      const key = String(lab || '').trim().toLowerCase();
      if (key && key !== '—' && !labSet.has(key)) labSet.set(key, String(lab).trim());
    };
    COMMON_LABS.forEach(addLab);
    (models || []).forEach((model) => addLab(modelLab(model)));
    const labs = [...labSet.values()].sort((a, b) => a.localeCompare(b));
    select.innerHTML = [
      '<option value="">All labs</option>',
      ...labs.map((lab) => `<option value="${escapeHtml(lab)}">${escapeHtml(lab)}</option>`),
    ].join('');
    select.value = previous && labs.some((lab) => lab.toLowerCase() === previous.toLowerCase()) ? previous : '';
    window.DFlashSelectTheme?.enhanceAll?.(select.closest('.df-catalog-toolbar'));
  }

  function listSizeLabel(model) {
    const label = String(model?.size_label || '').trim();
    if (label && !/^(?:—|-)$/.test(label)) return label;
    if (model?.size_gb != null) return `${model.size_gb} GB`;
    return '—';
  }

  function listAgeLabel(model) {
    if (model?.updated_days != null) return `${model.updated_days} days`;
    if (model?.updated_ago && model.updated_ago !== '—') return model.updated_ago;
    return '—';
  }

  function listDiskLabel(model) {
    const size = listSizeLabel(model);
    return size === '—' ? 'Disk —' : `Disk ${size}`;
  }

  function modelTitle(model) {
    return model.id || model.title || model.label || '—';
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

  function catalogReadyToLoad(model) {
    return !!model?.catalog_ready_to_load;
  }

  function catalogInstalled(model) {
    if (model?.local_ready || model?.catalog_ready_to_load) return true;
    const map = model?.local_installs || {};
    return Object.values(map).some((rows) => Array.isArray(rows) && rows.length > 0);
  }

  function catalogInstalledBadge() {
    // Plain "Installed" text — found on this PC via a local folder (e.g. LM
    // Studio). Never use the DFlash logo here: that would wrongly imply the
    // model lives in DFlash Console.
    return '<span class="lm-tag green" title="Installed locally on this PC">Installed</span>';
  }

  function catalogBadge(label, tone = 'blue', title = '') {
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
    return `<span class="lm-tag ${tone}"${titleAttr}>${escapeHtml(label)}</span>`;
  }

  function catalogDflashLabel() {
    return '<span class="lm-tag gold dflash-logo-label" role="img" aria-label="DFlash" title="DFlash speculative decoding stack"></span>';
  }

  function catalogDflashCompatible(model) {
    const category = String(model?.category || currentCategory()).toLowerCase();
    return category === 'dflash' || /dflash|dspark/i.test(catalogHaystack(model));
  }

  function catalogDflashCompatibleBadge() {
    return catalogBadge(
      'DFlash compatible',
      'gold',
      'Download a GGUF file first, then create the DFlash stack from the Models tab',
    );
  }

  function catalogListHasGguf(model) {
    const tags = Array.isArray(model?.tags)
      ? model.tags.map((tag) => String(tag || '').trim().toLowerCase())
      : [];
    return model?.has_gguf === true
      || Number(model?.gguf_count || 0) > 0
      || tags.some((tag) => tag === 'gguf' || tag.includes('gguf'));
  }

  function catalogListIsFullModelRepo(model) {
    if (catalogListHasGguf(model)) return false;
    const tags = Array.isArray(model?.tags)
      ? model.tags.map((tag) => String(tag || '').trim().toLowerCase())
      : [];
    return tags.some((tag) => tag === 'safetensors' || tag === 'transformers');
  }

  function catalogListTaskLabel(model) {
    const task = String(model?.pipeline_tag || '').trim();
    if (task) return `${task} · Hugging Face model`;
    const modalityLabels = {
      llm: 'Language model',
      embedding: 'Embedding model',
      vision: 'Vision model',
      'speech-to-text': 'Speech model',
      'text-to-speech': 'Speech model',
    };
    const modality = modalityLabels[String(model?.modality || '').trim()];
    return modality ? `${modality} · Hugging Face model` : 'Hugging Face model';
  }

  function catalogListKindBadge(model) {
    const tags = Array.isArray(model?.tags)
      ? model.tags.map((tag) => String(tag || '').trim().toLowerCase())
      : [];
    const id = String(model?.id || '').toLowerCase();
    const hasAcceleratorMarker = model?.accelerator_only === true
      || tags.some((tag) => /dflash|dspark|draft-model|speculative-decoding|speculator|eagle3/.test(tag))
      || /(?:-dflash(?:[-_.]|\/|$)|-dspark(?:[-_.]|\/|$)|eagle3)/i.test(id);
    if (hasAcceleratorMarker) {
      return catalogBadge(
        'ACCELERATOR',
        'gold',
        'Draft or accelerator checkpoint — pair it with its target model; it is not the full model',
      );
    }

    if (catalogListHasGguf(model)) {
      return catalogBadge('GGUF', 'blue', 'Quantized GGUF model file for llama.cpp-compatible runtimes');
    }

    if (catalogListIsFullModelRepo(model)) {
      return catalogBadge(
        'FULL MODEL',
        'purple',
        'Full model weights, usually SafeTensors/Transformers — not an accelerator-only checkpoint',
      );
    }

    return '';
  }

  function catalogListBadges(model) {
    const kind = catalogListKindBadge(model);
    const compatible = catalogDflashCompatible(model) && !kind
      ? catalogDflashCompatibleBadge()
      : '';
    return `${catalogInstalled(model) ? catalogInstalledBadge() : ''}${kind}${compatible}`;
  }

  function catalogListShowsNotRunnableNote(model) {
    if (model?.runnable === true) return false;
    if (catalogReadyToLoad(model)) return false;
    if (catalogInstalled(model)) return false;
    if (model?.accelerator_only === true) return false;
    if (catalogListHasGguf(model)) return false;
    return catalogListIsFullModelRepo(model);
  }

  function catalogModelUrl(model) {
    const url = String(model?.url || '').trim();
    if (url) return url;
    const id = String(model?.id || '').trim();
    if (!id || !id.includes('/')) return '';
    return `https://huggingface.co/${id}`;
  }

  function hideCatalogContextMenu() {
    const menu = document.getElementById('hfCatalogContextMenu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    catalogContextModel = null;
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

  async function runCatalogContextCommand(cmd, model) {
    if (!model) return;
    if (cmd === 'copy-id') {
      await navigator.clipboard.writeText(model.id || '');
      toast('Repo id copied');
      return;
    }
    if (cmd === 'copy-url') {
      const url = catalogModelUrl(model);
      if (!url) return;
      await navigator.clipboard.writeText(url);
      toast('Hugging Face URL copied');
      return;
    }
    if (cmd === 'open-hf') {
      const url = catalogModelUrl(model);
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
      return;
    }
    if (cmd === 'metadata') {
      const modal = document.getElementById('modelMetadataModal');
      const pre = document.getElementById('modelMetadataBody');
      if (pre) pre.textContent = JSON.stringify(model, null, 2);
      modal?.classList.add('open');
      modal?.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      return;
    }
    if (cmd === 'create-stack') {
      await selectModel(model.id, { preferCache: true, backgroundDetail: false });
      document.getElementById('hfCreateStackBtn')?.click();
      return;
    }
    if (cmd === 'refresh-detail') {
      void selectModel(model.id, { preferCache: false, backgroundDetail: false });
    }
  }

  function openCatalogContextMenu(event, model) {
    const menu = document.getElementById('hfCatalogContextMenu');
    if (!menu || !model?.id) return;
    catalogContextModel = model;
    const hfUrl = catalogModelUrl(model);
    const canStack = catalogDflashCompatible(model) || catalogListHasGguf(model);
    menu.innerHTML = `
      <button type="button" data-cmd="copy-id">Copy identifier</button>
      <button type="button" data-cmd="copy-url"${hfUrl ? '' : ' disabled'}>Copy Hugging Face URL</button>
      <button type="button" data-cmd="open-hf"${hfUrl ? '' : ' disabled'}>Open Hugging Face</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <hr>
      <button type="button" data-cmd="create-stack"${canStack ? '' : ' disabled'}>Create DFlash stack</button>
      <button type="button" data-cmd="refresh-detail">Refresh details</button>`;
    positionContextMenu(menu, event);
    menu.querySelectorAll('button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', (clickEvent) => {
        clickEvent.stopPropagation();
        void runCatalogContextCommand(btn.dataset.cmd, model);
        hideCatalogContextMenu();
      });
    });
  }

  function catalogListNotRunnableNote(model) {
    if (!catalogListShowsNotRunnableNote(model)) return '';
    return `<span class="lm-search-item-run-note" title="This format cannot be loaded in DFlash Console yet. You can still browse and download files from Hugging Face.">Can't run here yet</span>`;
  }

  function catalogHaystack(model) {
    return [
      model?.id,
      model?.title,
      model?.description,
      model?.readme,
      ...(Array.isArray(model?.tags) ? model.tags : []),
    ].join(' ').toLowerCase();
  }

  function catalogDetailBadges(model) {
    const badges = [];
    const haystack = catalogHaystack(model);
    const hfTags = Array.isArray(model?.tags)
      ? model.tags
          .map((tag) => String(tag || '').toLowerCase())
          .filter((tag) => tag && !/^(base_model|license|region|arxiv|doi):/.test(tag))
      : [];
    const hasTag = (pattern) => hfTags.some((tag) => pattern.test(tag));
    const hasText = (pattern) => pattern.test(haystack);

    if (catalogDflashCompatible(model)) badges.push(catalogDflashCompatibleBadge());
    // The logo is reserved for a DFlash stack that is already registered and
    // loadable in this Console. Text in a README or a compatible tag is not
    // proof that this repository is installed locally.
    if (catalogReadyToLoad(model) && catalogDflashCompatible(model)) {
      badges.push(catalogDflashLabel());
    }
    if (catalogReadyToLoad(model)) badges.push(catalogBadge('ready to load', 'gold'));
    if (model?.runnable) {
      badges.push(catalogBadge('runnable', 'green', 'Runnable in DFlash Console'));
    } else if (model?.downloadable && model?.modality && model?.modality !== 'llm') {
      badges.push(catalogBadge('download-only', 'yellow', 'Downloadable, but no Console runtime is installed for this modality yet'));
    }
    if (hasTag(/vision|multimodal|image-text|mmproj/) || hasText(/-vl-|mmproj|vision|multimodal/)) {
      badges.push(catalogBadge('vision', 'purple'));
    }
    if (hasTag(/tool|function-calling|agentic/) || hasText(/\btools?\b|function calling|agentic/)) {
      badges.push(catalogBadge('tools', 'green'));
    }
    if (hasTag(/reason|think|chain-of-thought|cot/) || hasText(/reasoning|\bthink\b|chain-of-thought/)) {
      badges.push(catalogBadge('reasoning', 'yellow'));
    }
    if (hasTag(/conversational|instruct|chat/) || hasText(/\binstruct\b|\bchat\b/)) {
      badges.push(catalogBadge('instruct', 'blue'));
    }
    if (hasTag(/speculative|draft-model|speculator/) || hasText(/speculative decoding|draft model/)) {
      badges.push(catalogBadge('speculative', 'purple'));
    }

    const paramTag = hfTags.find((tag) => /^\d+(?:\.\d+)?b$/i.test(tag) || /^\d+(?:\.\d+)?\s*b$/i.test(tag));
    const paramMatch = haystack.match(/\b(\d+(?:\.\d+)?)\s*b\b/);
    if (paramTag) badges.push(catalogBadge(paramTag.replace(/\s+/g, '').toUpperCase(), 'blue'));
    else if (paramMatch) badges.push(catalogBadge(`${paramMatch[1].toUpperCase()}B`, 'blue'));

    const archHaystack = haystack.replace(/llama\.cpp/g, ' ');
    for (const arch of ['gemma', 'qwen', 'mistral', 'deepseek', 'phi', 'llama']) {
      if (hasTag(new RegExp(`^${arch}\\d*`)) || new RegExp(`\\b${arch}\\b`).test(archHaystack)) {
        badges.push(catalogBadge(arch, 'blue'));
        break;
      }
    }

    const files = model?.download_files || model?.gguf_files || [];
    const format = files[0]?.format || (model?.has_gguf ? 'gguf' : '');
    if (format) badges.push(catalogBadge(String(format).toUpperCase(), 'blue'));

    return badges.join('');
  }

  function searchCacheKey(query, sort, category) {
    const installed = installedOnly() ? '1' : '0';
    return `${category}|${sort}|${installed}|${query}`;
  }

  function detailCacheKey(repoId, category) {
    return `${category}|${repoId}`;
  }

  function loadPersistedSearch(key) {
    try {
      const stored = JSON.parse(localStorage.getItem(SEARCH_CACHE_STORAGE_KEY) || '{}');
      const row = stored?.[key];
      return Array.isArray(row?.models) && row.models.length ? row : null;
    } catch {
      return null;
    }
  }

  function persistSearchCache(key, value) {
    try {
      const stored = JSON.parse(localStorage.getItem(SEARCH_CACHE_STORAGE_KEY) || '{}');
      stored[key] = {
        models: value.models,
        fetchedAt: value.fetchedAt,
        detailById: {},
      };
      const entries = Object.entries(stored)
        .sort(([, a], [, b]) => Number(b?.fetchedAt || 0) - Number(a?.fetchedAt || 0))
        .slice(0, 12);
      localStorage.setItem(SEARCH_CACHE_STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)));
    } catch {
      /* A full browser cache must never block catalog rendering. */
    }
  }

  function getCachedSearch(query, sort, category) {
    const key = searchCacheKey(query, sort, category);
    const memory = searchCache.get(key);
    if (memory) return memory;
    const persisted = loadPersistedSearch(key);
    if (persisted) {
      searchCache.set(key, persisted);
      return persisted;
    }
    return null;
  }

  function putCachedSearch(query, sort, category, nextModels) {
    const key = searchCacheKey(query, sort, category);
    const prior = searchCache.get(key);
    const value = {
      models: nextModels,
      fetchedAt: Date.now(),
      detailById: prior?.detailById || {},
    };
    searchCache.set(key, value);
    persistSearchCache(key, value);
  }

  function persistCurrentListMetadata() {
    const key = searchCacheKey(searchInput()?.value?.trim() || '', currentSort(), currentCategory());
    const cached = searchCache.get(key);
    if (!cached) return;
    cached.models = models;
    persistSearchCache(key, cached);
  }

  function loadingCopy(category) {
    return {
      listTitle: 'Loading model catalog',
      listSub: `Fetching ${categoryLabel(category)} from Hugging Face. This usually takes a few seconds.`,
      detailTitle: 'Loading model details',
      detailSub: 'README, GGUF files, and install status will appear here shortly.',
    };
  }

  function renderCatalogLoading(target = 'both', copy = {}) {
    const title = copy.listTitle || 'Loading model catalog';
    const subtitle = copy.listSub || 'Fetching models from Hugging Face…';
    const block = `
      <div class="df-catalog-loading" role="status" aria-live="polite">
        <div class="df-catalog-loading-spinner" aria-hidden="true"></div>
        <p class="df-catalog-loading-title">${escapeHtml(title)}</p>
        <p class="df-catalog-loading-sub">${escapeHtml(subtitle)}</p>
      </div>`;
    if (target === 'list' || target === 'both') {
      const list = searchList();
      if (list) list.innerHTML = block;
    }
    if (target === 'detail' || target === 'both') {
      renderDetailLoading(copy.detailTitle, copy.detailSub);
    }
  }

  function renderDetailLoading(title, subtitle) {
    const pane = detailPane();
    if (!pane) return;
    pane.innerHTML = `
      <div class="lm-search-placeholder df-catalog-loading df-catalog-loading-detail" role="status" aria-live="polite">
        <div class="df-catalog-loading-spinner" aria-hidden="true"></div>
        <p class="df-catalog-loading-title">${escapeHtml(title || 'Loading model details')}</p>
        <p class="df-catalog-loading-sub">${escapeHtml(subtitle || 'Reading README and file list from Hugging Face…')}</p>
      </div>`;
  }

  function setListRefreshIndicator(on) {
    const list = searchList();
    if (!list) return;
    if (!listRefreshIndicator) {
      listRefreshIndicator = document.createElement('div');
      listRefreshIndicator.id = 'hfCatalogListRefresh';
      listRefreshIndicator.className = 'df-catalog-list-refresh hidden';
      listRefreshIndicator.setAttribute('role', 'status');
      listRefreshIndicator.setAttribute('aria-live', 'polite');
      listRefreshIndicator.textContent = 'Refreshing catalog…';
      list.parentElement?.insertBefore(listRefreshIndicator, list);
    }
    listRefreshIndicator.classList.toggle('hidden', !on);
  }

  function restoreVisibleSelection({ preferCache = true } = {}) {
    const visible = visibleModels();
    if (selectedId && visible.some((model) => model.id === selectedId)) {
      void selectModel(selectedId, { preferCache, backgroundDetail: preferCache });
      return;
    }
    selectedId = '';
    selectedDetail = null;
    if (visible[0]) void selectModel(visible[0].id, { preferCache, backgroundDetail: preferCache });
    else renderDetailPlaceholder('Select a model to view details, README, and download GGUF files.');
  }

  function requestDetail(repoId, category) {
    const key = detailCacheKey(repoId, category);
    const cached = detailCache.get(key);
    if (cached) return Promise.resolve(cached);
    const pending = listDetailPending.get(key);
    if (pending) return pending;

    let request;
    request = api(
      `/api/hf/models/${encodeURIComponent(repoId)}?category=${encodeURIComponent(category)}`,
      { timeoutMs: 60000 },
    ).then((data) => {
      if (!data?.model) throw new Error('Model details unavailable');
      detailCache.set(key, data.model);
      return data.model;
    }).finally(() => {
      if (listDetailPending.get(key) === request) listDetailPending.delete(key);
    });
    listDetailPending.set(key, request);
    return request;
  }

  function mergeCatalogListDetail(repoId, detail) {
    const row = models.find((model) => model.id === repoId);
    if (!row || !detail) return false;
    const fields = [
      'size_gb',
      'size_label',
      'size_bytes',
      'accelerator_only',
      'has_gguf',
      'gguf_count',
      'file_count',
      'has_files',
      'local_ready',
      'local_installs',
      'catalog_ready_to_load',
      'runnable',
      'download_files',
      'gguf_files',
      'tags',
    ];
    let changed = false;
    fields.forEach((field) => {
      if (detail[field] === undefined || row[field] === detail[field]) return;
      row[field] = detail[field];
      changed = true;
    });
    return changed;
  }

  async function prefetchDetail(repoId, category) {
    try {
      return await requestDetail(repoId, category);
    } catch {
      /* warm-cache best effort */
      return null;
    }
  }

  let listWarmRenderTimer = null;

  function scheduleListWarmRender() {
    if (listWarmRenderTimer) return;
    listWarmRenderTimer = window.setTimeout(() => {
      listWarmRenderTimer = null;
      renderList();
    }, 120);
  }

  async function warmListDetails(rows, category) {
    const candidates = (rows || []).filter((model) => listSizeLabel(model) === '—');
    if (!candidates.length) return;

    const run = ++listDetailWarmGen;
    let cursor = 0;
    let changed = false;
    const worker = async () => {
      while (cursor < candidates.length) {
        const model = candidates[cursor];
        cursor += 1;
        const detail = await prefetchDetail(model.id, category);
        if (run !== listDetailWarmGen) return;
        if (detail && mergeCatalogListDetail(model.id, detail)) {
          changed = true;
          persistCurrentListMetadata();
          scheduleListWarmRender();
        }
      }
    };
    await Promise.all(
      Array.from({ length: LIST_DETAIL_WARM_WORKERS }, () => worker()),
    );
    if (changed && run === listDetailWarmGen) {
      if (listWarmRenderTimer) {
        window.clearTimeout(listWarmRenderTimer);
        listWarmRenderTimer = null;
      }
      renderList();
    }
  }

  async function warmCatalogCache() {
    if (catalogPrimed) return;
    try {
      if (!modelLibraries.length) await loadLibraries();
      const category = DEFAULT_CATEGORY;
      const sort = 'downloads';
      const query = '';
      if (getCachedSearch(query, sort, category)) {
        catalogPrimed = true;
        void searchCatalog(query, sort, category)
          .then((data) => {
            const rows = data.models || [];
            putCachedSearch(query, sort, category, rows);
            if (
              document.body.dataset.activeView === 'catalog'
              && !searchInput()?.value?.trim()
              && currentCategory() === category
            ) {
              models = rows;
              populateCreatorFilter();
              renderList();
              restoreVisibleSelection({ preferCache: true });
            }
          })
          .catch(() => {});
        return;
      }
      const data = await searchCatalog('', sort, category);
      const rows = data.models || [];
      searchCache.set(key, { models: rows, fetchedAt: Date.now(), detailById: {} });
      catalogPrimed = true;
      if (rows[0]?.id) void prefetchDetail(rows[0].id, category);
    } catch {
      /* prefetch is best-effort */
    }
  }

  async function searchCatalog(query, sort, category) {
    const path = `/api/hf/search?q=${encodeURIComponent(query)}&sort=${encodeURIComponent(sort)}&category=${encodeURIComponent(category)}&limit=25`;
    try {
      return await api(path, { timeoutMs: 30000 });
    } catch (firstError) {
      // Hugging Face can transiently stall; retry once before showing an empty catalog.
      return api(path, { timeoutMs: 30000 }).catch(() => { throw firstError; });
    }
  }

  function renderListLoading(message) {
    const category = currentCategory();
    if (message) {
      const list = searchList();
      if (list) list.innerHTML = `<div class="lm-search-empty df-catalog-loading-message">${escapeHtml(message)}</div>`;
      return;
    }
    renderCatalogLoading('list', loadingCopy(category));
  }

  function renderList() {
    const list = searchList();
    const category = currentCategory();
    const visible = visibleModels();
    if (!visible.length) {
      const creator = currentCreator();
      const hint = installedOnly()
        ? 'No installed models in this search. Try another query or pick a different filter.'
        : creator
        ? `No ${creator} models in this search. Try another lab or clear the filter.`
        : `No models found for ${escapeHtml(categoryLabel(category))}. Try another search or category.`;
      list.innerHTML = `<div class="lm-search-empty">${escapeHtml(hint)}</div>`;
      return;
    }
    list.innerHTML = visible.map((model) => {
      const selected = model.id === selectedId ? ' selected' : '';
      const ready = catalogReadyToLoad(model) ? ' ready-to-load' : '';
      const listBadges = catalogListBadges(model);
      const description = modelDescription(model);
      const labName = modelLab(model);
      const descLine = description
        ? `<span class="lm-search-item-summary">${escapeHtml(description)}</span>`
        : `<span class="lm-search-item-summary">${escapeHtml(catalogListTaskLabel(model))}</span>`;
      const metaLine = description
        ? `<span class="lm-search-item-desc">
            <span class="lm-search-item-author">${escapeHtml(labName)}</span>
            · ${escapeHtml(model.downloads_label || '0')} downloads${model.size_label && model.size_label !== '—' ? ` · ${escapeHtml(model.size_label)}` : ''}
          </span>`
        : `<span class="lm-search-item-desc">
            <span class="lm-search-item-author">${escapeHtml(labName)}</span>
            · ${escapeHtml(model.downloads_label || '0')} downloads${model.size_label && model.size_label !== '—' ? ` · ${escapeHtml(model.size_label)}` : ''}
          </span>`;
      return `
        <button type="button" class="lm-search-item${selected}${ready}" data-repo-id="${escapeHtml(model.id)}">
          ${avatarImg(model.author, model.author_avatar_url, 'lm-hf-avatar sm')}
          <div class="lm-search-item-main">
            <span class="lm-search-item-name">${escapeHtml(modelTitle(model))}</span>
            ${descLine}
            ${metaLine}
            ${catalogListNotRunnableNote(model)}
          </div>
          <div class="lm-search-item-aside">
            <div class="lm-search-item-badge-slot">${listBadges}</div>
            <div class="lm-search-item-stats">
              <span class="lm-search-item-stat lm-search-item-stat-age" title="Hugging Face last update">${escapeHtml(listAgeLabel(model))}</span>
              <span class="lm-search-item-stat lm-search-item-stat-disk" title="Approximate downloadable model size on disk">${escapeHtml(listDiskLabel(model))}</span>
            </div>
          </div>
        </button>`;
    }).join('');

    list.querySelectorAll('.lm-search-item').forEach((btn) => {
      btn.addEventListener('click', () => {
        hideCatalogContextMenu();
        void selectModel(btn.dataset.repoId, { preferCache: true, backgroundDetail: true });
      });
      btn.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const model = models.find((row) => row.id === btn.dataset.repoId);
        if (model) openCatalogContextMenu(event, model);
      });
    });
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

  async function resolveAnyLocalInstall(model) {
    if (!model?.id) return null;
    const known = Object.values(model.local_installs || {}).flatMap((rows) => Array.isArray(rows) ? rows : []);
    if (known[0]) return known[0];
    try {
      const data = await api(`/api/hf/local-installs?repo_id=${encodeURIComponent(model.id)}`);
      const matches = data.matches || [];
      if (!model.local_installs) model.local_installs = {};
      matches.forEach((match) => {
        if (match.filename) model.local_installs[match.filename] = [match];
      });
      return matches[0] || null;
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
    const repoInstall = install || (model.local_ready ? await resolveAnyLocalInstall(model) : null);
    const installed = Boolean(repoInstall || catalogInstalled(model));
    const btn = document.getElementById('hfDownloadBtn');
    const saveNote = document.getElementById('hfSaveNote');
    const installedNote = document.getElementById('hfInstalledNote');
    const card = document.querySelector('.df-catalog-model-card');
    hideCardDownloadProgress();
    if (installed) {
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Installed';
        btn.dataset.action = 'installed';
      }
      saveNote?.classList.add('hidden');
      if (installedNote) {
        installedNote.classList.remove('hidden');
        installedNote.innerHTML = `
          <span class="df-catalog-installed-label">Already installed</span>
          <code class="df-catalog-installed-path">${escapeHtml(repoInstall?.path || 'Installed locally; path unavailable')}</code>`;
      }
      card?.classList.toggle('ready-to-load', catalogReadyToLoad(model));
    } else {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Download';
        btn.dataset.action = 'download';
      }
      saveNote?.classList.remove('hidden');
      installedNote?.classList.add('hidden');
      if (installedNote) installedNote.innerHTML = '';
      card?.classList.remove('ready-to-load');
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
    const buttonTools = document.getElementById('hfDownloadButtonTools');
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
    pctEl?.classList.remove('hidden');
    if (fillEl) {
      fillEl.classList.toggle('is-indeterminate', indeterminate);
      fillEl.style.width = width != null ? `${width}%` : '';
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Downloading...';
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
      pctEl?.classList.add('hidden');
      if (btn) buttonTools?.appendChild(btn);
      if (statusEl) {
        statusEl.textContent = `Saved to ${job.path || 'models folder'}`;
        statusEl.classList.remove('hidden');
      }
      if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
      void refreshInstallUI(model);
    } else if (job.status === 'error') {
      hideCardDownloadProgress();
      pctEl?.classList.add('hidden');
      if (btn) buttonTools?.appendChild(btn);
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
      `<option value="${escapeHtml(file.filename)}"${idx === 0 ? ' selected' : ''}>${escapeHtml(file.label)}</option>`,
    ).join('');
    const initialSize = formatCatalogFileSize(files[0]);
    const fileSizeEl = files.length
      ? `<span class="df-catalog-file-size${initialSize ? '' : ' hidden'}" id="hfSelectedFileSize" title="${initialSize ? `File size on disk: ${escapeHtml(initialSize)}` : ''}">${escapeHtml(initialSize)}</span>`
      : '';
    const filePick = files.length > 1
      ? `<div class="df-catalog-file-row">
          <label class="df-catalog-field-label" for="hfFilePick">Quantization</label>
          <select class="lm-select small" id="hfFilePick">${fileOptions}</select>
        </div>`
      : (files.length === 1
        ? `<input type="hidden" id="hfFilePick" value="${escapeHtml(files[0].filename)}">`
        : '');
    const downloadBtn = files.length
      ? '<button class="lm-btn hf-primary hf-download-btn" type="button" id="hfDownloadBtn" data-action="download" title="Download the selected GGUF file from Hugging Face">↓ Download GGUF</button>'
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
      <div class="df-catalog-model-card${catalogReadyToLoad(model) ? ' ready-to-load' : ''}">
        <div class="lm-search-detail-head">
          <div class="lm-search-detail-top">
            ${avatarImg(model.author, model.author_avatar_url, 'lm-hf-avatar')}
            <div class="lm-search-detail-identity">
              <div class="lm-search-detail-title-row">
                <div class="lm-search-detail-title-group">
                  <h2 title="${escapeHtml(model.id)}">${escapeHtml(modelTitle(model))}</h2>
                  <button class="lm-icon-btn tiny lm-search-copy-repo" type="button" id="hfCopyRepo" title="Copy repo id" aria-label="Copy repo id">⧉</button>
                </div>
                <div class="lm-search-detail-tools" id="hfDownloadButtonTools">
                  <button class="lm-btn ghost small" type="button" id="hfCreateStackBtn">Create DFlash stack</button>
                  <a class="lm-btn ghost small" href="${escapeHtml(model.url || catalogModelUrl(model))}" target="_blank" rel="noopener noreferrer">Open HF</a>
                </div>
              </div>
              <div class="df-catalog-model-badges">${catalogDetailBadges(model)}</div>
              <p class="lm-search-stats">
                <span class="lm-search-item-author">${escapeHtml(modelLab(model))}</span>
                · ${escapeHtml(model.author || '—')}
                · ${escapeHtml(model.downloads_label || '0')} downloads · ★ ${model.likes || 0}
                · Updated ${escapeHtml(model.updated_ago || '—')}${model.size_label && model.size_label !== '—' ? ` · ${escapeHtml(model.size_label)}` : ''}
              </p>
              <p class="lm-search-repo-id">${escapeHtml(model.id)}</p>
              <p class="lm-search-description">${renderDescriptionHtml(summaryText)}</p>
              <div class="df-catalog-quant-download-row">
                ${filePick}
                ${fileSizeEl}
                <div class="df-catalog-quant-download-actions">
                  ${downloadBtn}
                  <span class="df-catalog-download-progress-pct hidden" id="hfDownloadProgressPct">0%</span>
                </div>
                <div class="df-catalog-download-progress hidden" id="hfDownloadProgress">
                  <div class="df-catalog-download-progress-head">
                    <span id="hfDownloadProgressLabel">Downloading…</span>
                  </div>
                  <div class="df-catalog-download-progress-bar">
                    <div class="df-catalog-download-progress-fill" id="hfDownloadProgressFill"></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
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
    document.getElementById('hfCreateStackBtn')?.addEventListener('click', async () => {
      const filename = getSelectedFilename();
      if (!filename) {
        toast('Pick a GGUF file first', false);
        return;
      }
      const install = await resolveLocalInstall(model, filename);
      const path = install?.path || '';
      const isAccel = /dflash|dspark/i.test(filename);
      if (!path) {
        toast('Download this file first, then create the stack from Models or reopen this button.', false);
        return;
      }
      window.DFlashStackWizard?.open?.({
        targetPath: isAccel ? '' : path,
        targetLabel: isAccel ? '' : filename,
        draftPath: isAccel ? path : '',
        draftLabel: isAccel ? filename : '',
      });
    });
    document.getElementById('hfFilePick')?.addEventListener('change', () => {
      updateSelectedFileSize(files);
      void refreshInstallUI(model);
    });
    document.getElementById('hfDownloadBtn')?.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();
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
    updateSelectedFileSize(files);
    void refreshInstallUI(model);
  }

  function renderDetailPlaceholder(message) {
    const pane = detailPane();
    if (!pane) return;
    pane.innerHTML = `<div class="lm-search-placeholder"><p>${escapeHtml(message || 'Select a model to view details, README, and download GGUF files.')}</p></div>`;
  }

  async function runSearch({ background = false } = {}) {
    const query = searchInput()?.value?.trim() || '';
    const sort = currentSort();
    const category = currentCategory();
    const copy = loadingCopy(category);
    const cached = getCachedSearch(query, sort, category);
    const canShowCached = !!(cached?.models?.length);

    if (canShowCached && !background) {
      models = cached.models;
      populateCreatorFilter();
      renderList();
      void warmListDetails(visibleModels(), category);
      restoreVisibleSelection({ preferCache: true });
      background = true;
    } else if (!background) {
      renderCatalogLoading('both', copy);
    }

    if (background) setListRefreshIndicator(true);

    const gen = ++searchRefreshGen;
    try {
      const data = await searchCatalog(query, sort, category);
      if (gen !== searchRefreshGen) return;
      models = data.models || [];
      putCachedSearch(query, sort, category, models);
      populateCreatorFilter();
      if (!models.some((m) => m.id === selectedId)) {
        selectedId = '';
        selectedDetail = null;
        if (!background) renderDetailPlaceholder();
      }
      renderList();
      void warmListDetails(visibleModels(), category);
      const visible = visibleModels();
      if (!selectedId && visible[0]) {
        await selectModel(visible[0].id, { preferCache: background, backgroundDetail: background });
      } else if (selectedId && !visible.some((model) => model.id === selectedId)) {
        if (visible[0]) await selectModel(visible[0].id, { preferCache: background, backgroundDetail: background });
        else renderDetailPlaceholder();
      }
    } catch (err) {
      if (gen !== searchRefreshGen) return;
      if (canShowCached) return;
      models = [];
      const message = /not found|404/i.test(err.message)
        ? 'Hugging Face search is unavailable. Restart DFlash Console, then try again.'
        : err.message;
      renderListLoading(message);
      renderDetailPlaceholder(message);
    } finally {
      if (gen === searchRefreshGen) setListRefreshIndicator(false);
    }
  }

  function scheduleSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      searchTimer = null;
      void runSearch();
    }, 350);
  }

  async function selectModel(repoId, { preferCache = false, backgroundDetail = false } = {}) {
    if (!repoId) return;
    selectedId = repoId;
    renderList();
    const category = currentCategory();
    const cacheKey = detailCacheKey(repoId, category);
    const cachedDetail = detailCache.get(cacheKey);
    if (preferCache && cachedDetail) {
      selectedDetail = cachedDetail;
      renderDetail(selectedDetail);
      if (backgroundDetail) void refreshDetail(repoId, category);
      return;
    }
    renderDetailLoading();
    await refreshDetail(repoId, category);
  }

  async function refreshDetail(repoId, category) {
    try {
      const model = await requestDetail(repoId, category);
      if (mergeCatalogListDetail(repoId, model)) {
        persistCurrentListMetadata();
        renderList();
      }
      if (selectedId === repoId) {
        selectedDetail = model;
        renderDetail(selectedDetail);
      }
    } catch (err) {
      if (selectedId === repoId) {
        renderDetailPlaceholder(err.message);
        toast(err.message, false);
      }
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
    const storedWidth = window.DFlashUiLayout?.getNumber?.('hf_search_left_width');
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
        window.DFlashUiLayout?.setNumber?.('hf_search_left_width', width);
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
      window.DFlashUiLayout?.setNumber?.('hf_search_left_width', next);
    });
  }

  async function onViewEnter() {
    await loadLibraries();
    applyCatalogDefaults();
    const input = searchInput();
    input?.focus();

    const query = input?.value?.trim() || '';
    const sort = currentSort();
    const category = currentCategory();
    const cached = getCachedSearch(query, sort, category);
    if (cached?.models?.length) {
      models = cached.models;
      populateCreatorFilter();
      renderList();
      restoreVisibleSelection({ preferCache: true });
      void runSearch({ background: true });
      return;
    }

    void runSearch({ background: false });
  }

  function onListFilterChange() {
    const visible = visibleModels();
    if (selectedId && !visible.some((model) => model.id === selectedId)) {
      selectedId = '';
      selectedDetail = null;
      if (visible[0]) void selectModel(visible[0].id, { preferCache: true, backgroundDetail: true });
      else renderDetailPlaceholder();
    }
    renderList();
  }

  function onCreatorFilterChange() {
    onListFilterChange();
  }

  function bind() {
    searchInput()?.addEventListener('input', scheduleSearch);
    document.getElementById('hfSearchSort')?.addEventListener('change', () => {
      if (installedOnly()) onListFilterChange();
      else void runSearch();
    });
    document.getElementById('hfSearchCategory')?.addEventListener('change', () => void runSearch());
    document.getElementById('hfSearchCreator')?.addEventListener('change', onCreatorFilterChange);
    document.addEventListener('click', hideCatalogContextMenu);
    document.addEventListener('scroll', hideCatalogContextMenu, true);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') hideCatalogContextMenu();
    });
    const ready = window.DFlashUiLayout?.whenReady?.() ?? Promise.resolve();
    ready.then(() => setupSearchResize());
    if (queueUnsubscribe) queueUnsubscribe();
    queueUnsubscribe = window.DFlashDownloadQueue?.subscribe?.(handleDownloadQueueUpdate) || null;
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    window.setTimeout(() => void warmCatalogCache(), 300);
    window.setInterval(() => {
      catalogPrimed = false;
      void warmCatalogCache();
    }, CATALOG_REFRESH_MS);
  });

  window.DFlashModelSearchLive = { onViewEnter, runSearch, warmCatalogCache };
})();
