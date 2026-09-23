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
  const LIST_DETAIL_WARM_LIMIT = 25;
  const listDetailPending = new Map();

  const DEFAULT_CATEGORY = 'all';
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

  /** Well-known Hugging Face orgs and GGUF quantizers (repo author before the slash). */
  const COMMON_UPLOADERS = [
    'Qwen',
    'google',
    'meta-llama',
    'microsoft',
    'mistralai',
    'deepseek-ai',
    'nvidia',
    'bartowski',
    'unsloth',
    'lmstudio-community',
    'TheBloke',
    'mradermacher',
    'QuantFactory',
    'RichardErkhov',
    'DevQuasar',
    'ggml-org',
    'MaziyarPanahi',
    'TomGrc',
    'DavidAU',
    'tensorblock',
    'NeuralBeaver',
    'SanjiWatsuki',
    'hugging-quants',
    'cognitivecomputations',
    'prithivMLmods',
    'NousResearch',
    'mlx-community',
    'z-lab',
    'huggingface',
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
      img.removeAttribute('width');
      img.removeAttribute('height');
      img.style.maxWidth = '100%';
      img.style.height = 'auto';
      img.style.width = 'auto';
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

  function markReadmeHero(container) {
    const img = container.querySelector('img:not(.lm-readme-badge)');
    if (!img) return;
    const hero = img.closest('p, div') || img;
    hero.classList.add('lm-readme-hero');
    const linksInHero = hero.querySelectorAll('a');
    if (linksInHero.length >= 2) {
      hero.classList.add('lm-readme-hero-links');
      return;
    }
    let next = hero.nextElementSibling;
    while (next && !String(next.textContent || '').trim() && !next.querySelector('a, img')) {
      next = next.nextElementSibling;
    }
    if (!next || next.querySelector('img:not(.lm-readme-badge)')) return;
    const links = next.querySelectorAll('a');
    const text = String(next.textContent || '').replace(/\s+/g, ' ').trim();
    if (links.length >= 2 && text.length < 320) {
      next.classList.add('lm-readme-hero-links');
    }
  }

  function wrapReadmeCodeBlocks(container) {
    container?.querySelectorAll('pre').forEach((pre) => {
      if (pre.parentElement?.classList.contains('lm-readme-pre-wrap')) return;
      const wrap = document.createElement('div');
      wrap.className = 'lm-readme-pre-wrap';
      pre.parentNode?.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lm-readme-copy';
      btn.textContent = 'Copy';
      btn.title = 'Copy code';
      btn.setAttribute('aria-label', 'Copy code');
      wrap.appendChild(btn);
    });
  }

  function decorateReadme(container) {
    if (!container) return;
    markReadmeHero(container);
    wrapReadmeCodeBlocks(container);
  }

  function copyReadmeCode(text) {
    const value = String(text || '');
    if (!value) return Promise.reject(new Error('empty'));
    if (navigator.clipboard?.writeText) {
      return navigator.clipboard.writeText(value).catch(() => {
        const area = document.createElement('textarea');
        area.value = value;
        area.setAttribute('readonly', '');
        area.style.position = 'fixed';
        area.style.left = '-9999px';
        document.body.appendChild(area);
        area.select();
        const ok = document.execCommand('copy');
        area.remove();
        if (!ok) throw new Error('copy failed');
      });
    }
    return Promise.reject(new Error('clipboard unavailable'));
  }

  function bindReadmeCopyButtons(root) {
    root?.querySelectorAll('.lm-readme-copy').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const pre = btn.closest('.lm-readme-pre-wrap')?.querySelector('pre');
        const text = String(pre?.innerText || pre?.textContent || '').replace(/\n$/, '');
        if (!text) return;
        copyReadmeCode(text).then(() => {
          const previous = btn.textContent;
          btn.textContent = 'Copied';
          btn.classList.add('is-copied');
          toast('Copied');
          window.setTimeout(() => {
            btn.textContent = previous || 'Copy';
            btn.classList.remove('is-copied');
          }, 1400);
        }).catch(() => toast('Copy failed', false));
      });
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
    decorateReadme(wrap);
    return wrap.outerHTML;
  }

  function textOnlyDescription(text) {
    return String(text || '')
      .replace(/<img\b[^>]*>/gi, '')
      .replace(/!\[[^\]]*]\([^)]+\)/g, '')
      .replace(/<br\s*\/?>/gi, ' ')
      .replace(/<\/(?:p|div|h[1-6])>/gi, ' ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function renderDescriptionHtml(text) {
    const raw = textOnlyDescription(text);
    if (!raw) return '';
    return escapeHtml(raw);
  }

  function formatHfFileSizeLabel(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return '';
    const gb = n / 1e9;
    if (gb >= 0.01) return `${gb.toFixed(2)} GB`;
    const mb = n / 1e6;
    if (mb >= 0.1) return `${mb.toFixed(2)} MB`;
    const kb = n / 1e3;
    return `${Math.max(1, Math.round(kb))} KB`;
  }

  function formatCatalogFileSize(file) {
    if (!file) return '';
    const explicit = String(file?.size_label || '').trim();
    if (explicit && explicit !== '—') return explicit;
    const bytes = Number(file.size_bytes);
    if (Number.isFinite(bytes) && bytes > 0) return formatHfFileSizeLabel(bytes);
    const gb = Number(file?.size_gb);
    if (Number.isFinite(gb) && gb > 0) return formatHfFileSizeLabel(gb * (1024 ** 3));
    return '';
  }

  function catalogFileOptionLabel(file) {
    const name = String(file?.filename || file?.label || '').trim();
    const size = formatCatalogFileSize(file);
    return size ? `${name} [${size}]` : name;
  }

  function catalogFileSizeBytes(file) {
    const bytes = Number(file?.size_bytes);
    if (Number.isFinite(bytes) && bytes > 0) return bytes;
    const gb = Number(file?.size_gb);
    if (Number.isFinite(gb) && gb > 0) return Math.round(gb * (1024 ** 3));
    return 0;
  }

  function catalogShardComponentLabel(filename) {
    const parts = String(filename || '').replaceAll('\\', '/').split('/');
    const component = String(parts.at(-2) || '').toLowerCase();
    const labels = {
      text_encoder: 'Text encoder',
      'text-encoder': 'Text encoder',
      transformer: 'Transformer weights',
      transformers: 'Transformer weights',
      vae: 'VAE',
      unet: 'UNet weights',
      tokenizer: 'Tokenizer',
    };
    return labels[component] || '';
  }

  const CATALOG_SHARD_RE = /^(?<prefix>.+?)-(?<part>\d{5})-of-(?<total>\d{5})\.(?:gguf|safetensors|bin)$/i;

  function isAuxiliaryCatalogFilename(filename) {
    const lower = String(filename || '').trim().toLowerCase();
    if (!lower) return false;
    if (lower.includes('imatrix')) return true;
    if (lower.startsWith('mmproj') || lower.includes('.mmproj') || lower.includes('/mmproj/')) return true;
    if (lower.startsWith('mtp-')) return true;
    if (lower.endsWith('.part')) return true;
    if (/(?:^|[._-])draft(?:[._-]|\.gguf$)/.test(lower)) return true;
    if (/(?:^|[._-])fastmtp(?:[._-]|\.gguf$)/.test(lower)) return true;
    return false;
  }

  function catalogHasWeightInstalls(model) {
    const map = model?.local_installs || {};
    return Object.entries(map).some(([name, rows]) =>
      !isAuxiliaryCatalogFilename(name) && Array.isArray(rows) && rows.length > 0,
    );
  }

  function groupCatalogDownloadFiles(files) {
    const list = Array.isArray(files) ? files.filter((file) => file?.filename) : [];
    const groups = new Map();
    const singles = [];
    list.forEach((file) => {
      const filename = String(file.filename || '');
      const base = filename.replace(/^.*[\\/]/, '');
      const match = base.match(CATALOG_SHARD_RE);
      if (!match?.groups) {
        singles.push(file);
        return;
      }
      const key = `${match.groups.prefix}|${match.groups.total}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(file);
    });

    const options = [];
    groups.forEach((rows) => {
      rows.sort((a, b) => String(a.filename).localeCompare(String(b.filename)));
      const first = rows[0];
      const base = String(first.filename || '').replace(/^.*[\\/]/, '');
      const match = base.match(CATALOG_SHARD_RE);
      if (!match?.groups) {
        singles.push(...rows);
        return;
      }
      const expected = Number(match.groups.total) || rows.length;
      const totalBytes = rows.reduce((sum, row) => sum + catalogFileSizeBytes(row), 0);
      const ext = base.split('.').pop().toLowerCase();
      const isGguf = ext === 'gguf';
      const prefix = match.groups.prefix;
      const componentLabel = catalogShardComponentLabel(first.filename);
      options.push({
        filename: first.filename,
        label: expected > 1
          ? (isGguf
            ? `${prefix} (${expected} files)`
            : `${componentLabel || 'Full model'} (${expected} files)`)
          : base,
        files: rows.map((row) => row.filename),
        kind: expected > 1 ? (isGguf ? 'quant' : 'sharded') : (isGguf ? 'quant' : 'file'),
        shard_count: expected,
        file_count: rows.length,
        size_bytes: totalBytes > 0 ? totalBytes : null,
        size_gb: totalBytes > 0 ? Math.round((totalBytes / (1024 ** 3)) * 100) / 100 : null,
        incomplete: rows.length < expected,
      });
    });

    singles.forEach((file) => {
      const filename = String(file.filename || '');
      const totalBytes = catalogFileSizeBytes(file);
      const ext = filename.split('.').pop().toLowerCase();
      options.push({
        filename,
        label: filename.replace(/^.*[\\/]/, ''),
        files: [filename],
        kind: ext === 'gguf' ? 'quant' : 'file',
        shard_count: 1,
        file_count: 1,
        size_bytes: totalBytes > 0 ? totalBytes : null,
        size_gb: totalBytes > 0 ? Math.round((totalBytes / (1024 ** 3)) * 100) / 100 : file.size_gb,
        incomplete: false,
      });
    });

    options.sort((a, b) => {
      const auxRank = (row) => (isAuxiliaryCatalogFilename(row.filename) ? 1 : 0);
      const kindRank = (row) => (row.kind === 'quant' ? 0 : 1);
      return auxRank(a) - auxRank(b)
        || kindRank(a) - kindRank(b)
        || String(a.label).localeCompare(String(b.label))
        || String(a.filename).localeCompare(String(b.filename));
    });
    return options;
  }

  function catalogModelTotalSize(model) {
    const files = model?.download_files || model?.gguf_files || [];
    const bytes = files.reduce((sum, file) => sum + catalogFileSizeBytes(file), 0);
    return bytes > 0 ? formatCatalogFileSize({ size_bytes: bytes }) : '';
  }

  function catalogDownloadOptions(model) {
    const files = model?.download_files || model?.gguf_files || [];
    return groupCatalogDownloadFiles(files);
  }

  function catalogDownloadFieldLabel(options) {
    if (!options.length) return 'Download';
    if (options.length === 1 && options[0].kind === 'sharded') return 'Download';
    if (options.every((opt) => opt.kind === 'quant')) return 'Quantization';
    return 'Download';
  }

  function catalogDownloadOptionLabel(opt) {
    const label = String(opt?.label || opt?.filename || '').trim();
    const size = formatCatalogFileSize(opt);
    if (!size) return label;
    if (opt?.kind === 'sharded' || (opt?.shard_count || 0) > 1) {
      return `${label} [${size} total]`;
    }
    return `${label} [${size}]`;
  }

  function catalogDownloadHint(model, options) {
    const rows = Array.isArray(options) ? options : catalogDownloadOptions(model);
    const sharded = rows.find((opt) => opt.kind === 'sharded' && (opt.shard_count || 0) > 1);
    if (sharded) {
      const size = formatCatalogFileSize(sharded);
      return size
        ? `One download — Console fetches all ${sharded.shard_count} files (${size} on disk).`
        : `One download — Console fetches all ${sharded.shard_count} files.`;
    }
    const total = catalogModelTotalSize(model);
    return total ? `Total download size: ${total}` : '';
  }

  function selectedDownloadOption(model) {
    const options = catalogDownloadOptions(model);
    const filename = getSelectedFilename();
    if (!filename) return options[0] || null;
    return options.find((opt) => opt.filename === filename) || options[0] || null;
  }

  function selectedCatalogFile(model) {
    const opt = selectedDownloadOption(model);
    if (opt) return opt;
    const list = Array.isArray(model?.download_files) ? model.download_files
      : (Array.isArray(model?.gguf_files) ? model.gguf_files : []);
    const filename = getSelectedFilename();
    if (!filename) return list[0] || null;
    return list.find((file) => file.filename === filename) || list[0] || null;
  }

  function updateSelectedFileSize(model) {
    const el = document.getElementById('hfSelectedFileSize');
    if (!el) return;
    const selected = selectedDownloadOption(model) || selectedCatalogFile(model);
    const size = formatCatalogFileSize(selected);
    el.textContent = size || '';
    el.classList.toggle('hidden', !size);
    el.title = size ? `Total download size: ${size}` : '';
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

  function currentUploader() {
    return document.getElementById('hfSearchUploader')?.value || '';
  }

  function installedOnly() {
    return document.getElementById('hfSearchSort')?.value === 'installed';
  }

  function fitsMachineOnly() {
    return document.getElementById('hfSearchSort')?.value === 'fits_machine';
  }

  function acceleratorsOnly() {
    return document.getElementById('hfSearchSort')?.value === 'accelerators';
  }

  function currentSort() {
    const value = document.getElementById('hfSearchSort')?.value || 'downloads';
    if (value === 'installed' || value === 'fits_machine' || value === 'accelerators') {
      return 'downloads';
    }
    return value;
  }

  function modelLab(model) {
    return model.lab || model.author || '—';
  }

  function modelUploader(model) {
    const author = String(model?.author || '').trim();
    if (author) return author;
    const repoId = String(model?.id || '').trim();
    return repoId.includes('/') ? repoId.split('/')[0] : '';
  }

  function visibleModels() {
    let rows = models;
    if (installedOnly()) {
      rows = rows.filter((model) => catalogInstalled(model));
    }
    if (acceleratorsOnly()) {
      rows = rows.filter((model) => model?.accelerator_only);
    }
    if (fitsMachineOnly()) {
      rows = rows.filter((model) => model?.fits_machine === true);
    }
    const lab = currentCreator();
    if (lab) {
      const needle = String(lab).trim().toLowerCase();
      rows = rows.filter((model) => String(modelLab(model)).trim().toLowerCase() === needle);
    }
    const uploader = currentUploader();
    if (uploader) {
      const needle = String(uploader).trim().toLowerCase();
      rows = rows.filter((model) => String(modelUploader(model)).trim().toLowerCase() === needle);
    }
    return pinCatalogRecommendations(rows);
  }

  const CATALOG_OFFICIAL_AUTHORS = new Set([
    'google', 'google-bert', 'google-t5', 'meta-llama', 'meta', 'facebook',
    'openai', 'mistralai', 'qwen', 'deepseek-ai', 'microsoft', 'ibm-granite',
    'nvidia', 'stabilityai', 'black-forest-labs', 'cohere', 'huggingface',
    'openai-community', 'baai', 'nomic-ai',
  ]);
  const CATALOG_QUALITY_GGUF_AUTHORS = new Set([
    'bartowski', 'unsloth', 'lmstudio-community', 'ggml-org', 'thebloke', 'google',
  ]);

  function catalogRepoSlug(model) {
    const repoId = String(model?.id || '').trim();
    return (repoId.includes('/') ? repoId.split('/').pop() : repoId).toLowerCase();
  }

  function catalogAuthor(model) {
    const author = String(model?.author || '').trim().toLowerCase();
    if (author) return author;
    const repoId = String(model?.id || '').trim();
    return repoId.includes('/') ? repoId.split('/')[0].toLowerCase() : '';
  }

  function catalogFormatKind(model) {
    if (model?.accelerator_only) return 'accel';
    if (catalogListHasGguf(model)) return 'gguf';
    return 'full';
  }

  function catalogFamilyKey(model) {
    let slug = catalogRepoSlug(model).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    let previous = '';
    while (slug && slug !== previous) {
      previous = slug;
      slug = slug
        .replace(/(?:-gguf)+$/i, '')
        .replace(/-(?:qat)(?:-.*)?$/i, '')
        .replace(/-(?:i?q)\d[\w-]*$/i, '')
        .replace(/-(?:q[2-8](?:[_-]k(?:[_-][sml])?|[_-]0)?)$/i, '');
    }
    return slug.replace(/^(?:google|meta|qwen)-/, '') || catalogRepoSlug(model);
  }

  function catalogLooksLikeSidecarRepo(model) {
    if (model?.accelerator_only) return true;
    const size = Number(model?.size_gb);
    if (!Number.isFinite(size)) return false;
    const slug = catalogRepoSlug(model);
    const param = slug.match(/\b(\d+(?:\.\d+)?)\s*b\b/i);
    if (!param) return false;
    return Number(param[1]) >= 7 && size < 6;
  }

  function catalogRecommendationScore(model) {
    const slug = catalogRepoSlug(model);
    const author = catalogAuthor(model);
    const kind = catalogFormatKind(model);
    let score = 0;
    if (model?.accelerator_only) score -= 600;
    if (model?.fits_machine === true) score += 1000;
    else if (model?.fits_machine_uncertain === true) score += 180;
    else score -= 350;
    if (model?.local_ready || model?.catalog_ready_to_load) score += 280;
    if (model?.runnable === true) score += 140;
    if (kind === 'gguf') {
      score += 220;
      if (CATALOG_QUALITY_GGUF_AUTHORS.has(author)) score += 50;
    }
    if (CATALOG_OFFICIAL_AUTHORS.has(author)) {
      score += 200;
      if (kind === 'full') score += 90;
    }
    if (/(?:^|-)(?:it|instruct)(?:-|$)/i.test(slug)) score += 45;
    if (/uncensored|abliterat|nsfw/i.test(slug)) score -= 90;
    const downloads = Number(model?.downloads) || 0;
    score += Math.min(Math.floor(Math.log10(downloads + 1) * 42), 260);
    return score;
  }

  function catalogRecommendationCanPair(existing, candidate) {
    const fam = catalogFamilyKey(candidate);
    const kind = catalogFormatKind(candidate);
    return existing.every((row) => {
      if (catalogFamilyKey(row) !== fam) return true;
      const other = catalogFormatKind(row);
      if (other === kind) return false;
      return new Set([other, kind]).size === 2 && other !== 'accel' && kind !== 'accel'
        && ((other === 'full' && kind === 'gguf') || (other === 'gguf' && kind === 'full'));
    });
  }

  function catalogRecommendationIsQuality(model) {
    const author = catalogAuthor(model);
    if (CATALOG_OFFICIAL_AUTHORS.has(author) || CATALOG_QUALITY_GGUF_AUTHORS.has(author)) return true;
    if (model?.local_ready || model?.catalog_ready_to_load) return true;
    return (Number(model?.downloads) || 0) >= 100000;
  }

  function orderCatalogRecommendations(picked) {
    const groups = new Map();
    const order = [];
    picked.forEach((model) => {
      const fam = catalogFamilyKey(model);
      if (!groups.has(fam)) {
        order.push(fam);
        groups.set(fam, []);
      }
      groups.get(fam).push(model);
    });
    const ordered = [];
    order.forEach((fam) => {
      const group = groups.get(fam) || [];
      group.sort((a, b) => {
        const kindA = catalogFormatKind(a) === 'full' ? 0 : 1;
        const kindB = catalogFormatKind(b) === 'full' ? 0 : 1;
        if (kindA !== kindB) return kindA - kindB;
        return catalogRecommendationScore(b) - catalogRecommendationScore(a);
      });
      ordered.push(...group);
    });
    return ordered;
  }

  function pinCatalogRecommendations(rows) {
    const list = Array.isArray(rows) ? rows.slice() : [];
    list.forEach((model) => {
      if (!model) return;
      delete model.catalog_recommended;
      delete model.catalog_recommended_rank;
      delete model.catalog_recommended_reason;
    });
    if (!list.length) return list;
    const ranked = list.slice().sort((a, b) => {
      const delta = catalogRecommendationScore(b) - catalogRecommendationScore(a);
      if (delta) return delta;
      return (Number(b?.downloads) || 0) - (Number(a?.downloads) || 0);
    });
    const picked = [];
    const skipAccel = !list.every((model) => model?.accelerator_only);
    const requireFit = list.some((model) => model?.fits_machine === true);
    ranked.forEach((model) => {
      if (picked.length >= 3) return;
      if (skipAccel && (model?.accelerator_only || catalogLooksLikeSidecarRepo(model))) return;
      if (!model?.has_gguf && !model?.has_files && !(Number(model?.downloads) > 0)) return;
      if (requireFit && model?.fits_machine !== true) return;
      if (picked.length && !catalogRecommendationIsQuality(model)) return;
      if (!catalogRecommendationCanPair(picked, model)) return;
      picked.push(model);
    });
    const ordered = orderCatalogRecommendations(picked);
    const reasons = {
      1: 'Best match for this PC — fits your GPU, popular, and a trusted source when possible',
      2: 'Strong alternate for this PC',
      3: 'Another good option for this PC',
    };
    ordered.forEach((model, index) => {
      model.catalog_recommended = true;
      model.catalog_recommended_rank = index + 1;
      model.catalog_recommended_reason = reasons[index + 1] || reasons[2];
    });
    const pickedIds = new Set(ordered.map((model) => model.id));
    return [...ordered, ...list.filter((model) => !pickedIds.has(model.id))];
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
    window.DFlashSelectTheme?.syncSelect?.(select);
    window.DFlashSelectTheme?.enhanceAll?.(select.closest('.df-catalog-toolbar'));
  }

  function populateUploaderFilter() {
    const select = document.getElementById('hfSearchUploader');
    if (!select) return;
    const previous = select.value;
    const uploaderSet = new Map();
    const addUploader = (name) => {
      const clean = String(name || '').trim();
      const key = clean.toLowerCase();
      if (key && key !== '—' && !uploaderSet.has(key)) uploaderSet.set(key, clean);
    };
    COMMON_UPLOADERS.forEach(addUploader);
    (models || []).forEach((model) => addUploader(modelUploader(model)));
    const uploaders = [...uploaderSet.values()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
    select.innerHTML = [
      '<option value="">All providers</option>',
      ...uploaders.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`),
    ].join('');
    select.value = previous && uploaders.some((name) => name.toLowerCase() === previous.toLowerCase())
      ? previous
      : '';
    window.DFlashSelectTheme?.syncSelect?.(select);
    window.DFlashSelectTheme?.enhanceAll?.(select.closest('.df-catalog-toolbar'));
  }

  function populateCatalogFilters() {
    populateCreatorFilter();
    populateUploaderFilter();
  }

  function estimateDiskSizeFromName(repoId, hasGguf) {
    const name = String(repoId || '').split('/').pop().toLowerCase().replace(/_/g, '-');
    if (!name) return null;
    const billions = [...name.matchAll(/(\d+(?:\.\d+)?)\s*b\b/gi)].map((match) => Number(match[1]));
    const millions = [...name.matchAll(/(?<![a-z0-9])(\d+(?:\.\d+)?)\s*m(?:-|$|\b)/gi)]
      .map((match) => Number(match[1]) / 1000);
    const params = [...billions, ...millions].filter((value) => Number.isFinite(value) && value > 0);
    if (!params.length) return null;
    const paramsB = Math.max(...params);
    if (paramsB > 2000) return null;
    const sizeGb = Math.round(paramsB * (hasGguf ? 0.55 : 2) * 100) / 100;
    if (sizeGb <= 0) return null;
    return { size_gb: sizeGb, size_label: `~${Number(sizeGb)} GB` };
  }

  function preferredCatalogOption(model) {
    const options = catalogDownloadOptions(model).filter((opt) => !isAuxiliaryCatalogFilename(opt.filename));
    if (!options.length) return null;
    const ranked = ['q4_k_m', 'q4_k_s', 'q5_k_m', 'q4_0', 'q6_k'];
    for (const token of ranked) {
      const match = options.find((opt) => String(opt.filename || '').toLowerCase().includes(token));
      if (match && formatCatalogFileSize(match)) return match;
    }
    return options.find((opt) => formatCatalogFileSize(opt)) || options[0];
  }

  function listSizeLabel(model) {
    const preferred = preferredCatalogOption(model);
    const fromQuant = formatCatalogFileSize(preferred);
    if (fromQuant) return fromQuant;
    const label = String(model?.size_label || '').trim();
    const sizeGb = Number(model?.size_gb);
    const slug = String(model?.id || model?.title || '').split('/').pop() || '';
    const param = slug.match(/(\d+(?:\.\d+)?)\s*b\b/i);
    const paramsB = param ? Number(param[1]) : 0;
    const looksTinyForBigModel = paramsB >= 7 && Number.isFinite(sizeGb) && sizeGb > 0 && sizeGb < 6;
    if (looksTinyForBigModel) {
      const estimated = estimateDiskSizeFromName(model?.id || model?.title, !!model?.has_gguf);
      if (estimated?.size_label) return estimated.size_label;
    }
    if (label && !/^(?:—|-)$/i.test(label) && !/^0(?:\.0+)?\s*gb$/i.test(label) && !looksTinyForBigModel) {
      return label;
    }
    if (Number.isFinite(sizeGb) && sizeGb > 0 && !looksTinyForBigModel) return `${sizeGb} GB`;
    const smallest = model?.smallest_quant_gb;
    if (smallest != null && smallest > 0 && !(paramsB >= 7 && smallest < 6)) return `${smallest} GB`;
    const estimated = estimateDiskSizeFromName(model?.id || model?.title, !!model?.has_gguf);
    return estimated?.size_label || '—';
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
    if (model?.local_ready) return true;
    return catalogHasWeightInstalls(model);
  }

  function catalogAuxiliaryOnly(model) {
    if (model?.local_auxiliary_only) return true;
    if (model?.local_ready || catalogHasWeightInstalls(model)) return false;
    const map = model?.local_installs || {};
    return Object.entries(map).some(([name, rows]) =>
      isAuxiliaryCatalogFilename(name) && Array.isArray(rows) && rows.length > 0,
    );
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

  function catalogFitsMachine(model) {
    return model?.fits_machine === true;
  }

  function catalogFitsMachineBadge(model) {
    if (!catalogFitsMachine(model)) return '';
    const shown = listSizeLabel(model);
    const title = shown && shown !== '—'
      ? `Fits your largest GPU VRAM (${shown})`
      : 'Fits your largest GPU VRAM';
    return `<span class="lm-tag green" title="${escapeHtml(title)}">Fits PC</span>`;
  }

  function catalogRecommendedBadge(model) {
    if (!model?.catalog_recommended) return '';
    const reason = String(model.catalog_recommended_reason || 'Best match for this PC').trim();
    return `<span class="lm-tag gold catalog-recommended" title="${escapeHtml(reason)}">Recommended</span>`;
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

  function catalogDraftPrimary(model) {
    if (model?.accelerator_only === true) return false;
    if (model?.catalog_draft_primary === true) return true;
    if (model?.catalog_draft_primary === false) return false;
    return catalogLooksLikeSidecarRepo(model);
  }

  function catalogListDraftBadge(model) {
    if (!catalogDraftPrimary(model)) return '';
    return catalogBadge(
      'DRAFT',
      'orange',
      'MTP or draft sidecar — not the full model. Open the repo and download a full quant (often ~13–15 GB for 27B).',
    );
  }

  function catalogListKindBadge(model) {
    const tags = Array.isArray(model?.tags)
      ? model.tags.map((tag) => String(tag || '').trim().toLowerCase())
      : [];
    const id = String(model?.id || '').toLowerCase();
    const hasAcceleratorMarker = model?.accelerator_only === true
      || window.DFlashModelCard?.isAccelerator?.(model) === true
      || tags.some((tag) => /dflash|dspark|draft-model|speculator|eagle3/.test(tag))
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
    const shared = window.DFlashModelCard?.classificationTags?.(model, {
      includeReasoning: false,
      includeLogo: false,
    }) || '';
    const kindBadge = catalogListKindBadge(model);
    const compatible = catalogDflashCompatible(model) && !kindBadge
      ? catalogDflashCompatibleBadge()
      : '';
    return `${shared}${catalogFitsMachineBadge(model)}${catalogInstalled(model) ? catalogInstalledBadge() : ''}${catalogAuxiliaryOnly(model) ? catalogBadge('calibration only', 'yellow', 'Only an imatrix/calibration file is present — download the model weights') : ''}${catalogListDraftBadge(model)}${kindBadge}${compatible}`;
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
    const shared = window.DFlashModelCard?.classificationTags?.(model) || '';
    const accelerator = window.DFlashModelCard?.isAccelerator?.(model) === true;
    const haystack = catalogHaystack(model);
    const hfTags = Array.isArray(model?.tags)
      ? model.tags
          .map((tag) => String(tag || '').toLowerCase())
          .filter((tag) => tag && !/^(base_model|license|region|arxiv|doi):/.test(tag))
      : [];
    const hasTag = (pattern) => hfTags.some((tag) => pattern.test(tag));
    const hasText = (pattern) => pattern.test(haystack);

    if (shared) badges.push(shared);
    if (catalogDraftPrimary(model)) {
      badges.push(catalogBadge(
        'draft',
        'orange',
        'MTP or draft sidecar — download a full quant from this repo to chat, not the draft file alone',
      ));
    }
    if (!accelerator && catalogDflashCompatible(model)) badges.push(catalogDflashCompatibleBadge());
    // The logo is reserved for a DFlash stack that is already registered and
    // loadable in this Console. Text in a README or a compatible tag is not
    // proof that this repository is installed locally.
    if (
      !window.DFlashModelCard?.isStack?.(model)
      && catalogReadyToLoad(model)
      && catalogDflashCompatible(model)
    ) {
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
    if (!accelerator && (hasTag(/reason|think|chain-of-thought|cot/) || hasText(/reasoning|\bthink\b|chain-of-thought/))) {
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

  const FIT_CACHE_VERSION = 'v7';

  function searchCacheKey(query, sort, category) {
    const installed = installedOnly() ? '1' : '0';
    return `${FIT_CACHE_VERSION}|${category}|${sort}|${installed}|${query}`;
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

  function normalizeHfSearchQuery(query) {
    let text = String(query || '')
      .replace(/[\u2013\u2014]/g, '-')
      .replace(/[\n\t]+/g, ' ')
      .trim();
    text = text.replace(/(?:[\s|]+-?\s*|\s+)[^\s/]+\.(?:gguf|safetensors|bin|pt|pth|onnx|ggml)\s*$/i, '').trim();
    text = text.replace(/^[\s|-]+|[\s|-]+$/g, '').replace(/\s+/g, ' ').trim();
    const first = text.split(' ')[0] || '';
    if (/^[\w][\w.-]*\/[\w][\w.-]*$/.test(first)) return first;
    return text;
  }

  function isRepoIdQuery(query) {
    const needle = normalizeHfSearchQuery(query).replace(/^\/+|\/+$/g, '');
    if (!needle || needle.includes(' ') || (needle.match(/\//g) || []).length !== 1) return false;
    return /^[\w][\w.-]*\/[\w][\w.-]*$/.test(needle);
  }

  function loadingCopy(category, query = '') {
    if (isRepoIdQuery(query)) {
      return {
        listTitle: 'Looking up model',
        listSub: `Resolving ${query} on Hugging Face…`,
        detailTitle: 'Loading model details',
        detailSub: 'README, GGUF files, and install status will appear here shortly.',
      };
    }
    if (String(query || '').trim()) {
      return {
        listTitle: 'Searching catalog',
        listSub: `Matching “${query}” in the local Hugging Face index…`,
        detailTitle: 'Loading model details',
        detailSub: 'The list appears first. README and files fill in next.',
      };
    }
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
    if (cached && !cached.detail_partial) return Promise.resolve(cached);
    const pending = listDetailPending.get(key);
    if (pending) return pending;

    let request;
    request = api(
      `/api/hf/models/${encodeURIComponent(repoId)}?category=${encodeURIComponent(category)}`,
      { timeoutMs: 20000 },
    ).then((data) => {
      if (!data?.model) throw new Error('Model details unavailable');
      const model = {
        ...data.model,
        detail_partial: Boolean(data.partial || data.model.detail_partial),
      };
      if (!model.detail_partial) detailCache.set(key, model);
      return model;
    }).finally(() => {
      if (listDetailPending.get(key) === request) listDetailPending.delete(key);
    });
    listDetailPending.set(key, request);
    return request;
  }

  const readmeInflight = new Map();

  function applyReadmeToSelection(repoId, text) {
    if (selectedId !== repoId) return;
    selectedDetail = { ...(selectedDetail || {}), id: repoId, readme: text, readme_pending: false };
    const cacheKey = detailCacheKey(repoId, currentCategory());
    const cached = detailCache.get(cacheKey);
    if (cached) detailCache.set(cacheKey, { ...cached, readme: text, readme_pending: false });
    renderDetail(selectedDetail);
  }

  function fillReadme(repoId, attempt = 0) {
    if (!repoId || attempt > 2) return Promise.resolve();
    if (String(selectedDetail?.readme || '').trim() && selectedDetail?.id === repoId) {
      return Promise.resolve();
    }
    const existing = readmeInflight.get(repoId);
    if (existing && attempt === 0) return existing;
    const req = api(
      `/api/hf/readme?repo_id=${encodeURIComponent(repoId)}`,
      { timeoutMs: 95000 },
    ).then((data) => {
      const text = String(data?.readme || '');
      if (text.trim()) {
        applyReadmeToSelection(repoId, text);
        return;
      }
      if (data?.pending && attempt < 2) {
        if (selectedId === repoId && selectedDetail) {
          selectedDetail = { ...selectedDetail, readme_pending: true };
          renderDetail(selectedDetail);
        }
        window.setTimeout(() => { void fillReadme(repoId, attempt + 1); }, 1200);
        return;
      }
      if (selectedId === repoId && selectedDetail) {
        selectedDetail = { ...selectedDetail, readme_pending: false };
        renderDetail(selectedDetail);
      }
    }).catch(() => {
      if (attempt < 2) window.setTimeout(() => { void fillReadme(repoId, attempt + 1); }, 1200);
    }).finally(() => {
      if (readmeInflight.get(repoId) === req) readmeInflight.delete(repoId);
    });
    readmeInflight.set(repoId, req);
    return req;
  }

  const filesInflight = new Map();
  const FILES_POLL_MAX_ATTEMPTS = 45;
  const FILES_POLL_DELAY_MS = 3000;

  function applyFilesToSelection(repoId, payload) {
    if (selectedId !== repoId || !payload) return;
    const downloadFiles = Array.isArray(payload.download_files) ? payload.download_files : [];
    const ggufFiles = Array.isArray(payload.gguf_files) ? payload.gguf_files : [];
    const files = downloadFiles.length ? downloadFiles : ggufFiles;
    const hasFiles = files.length > 0;
    const next = {
      ...(selectedDetail || {}),
      id: repoId,
      download_files: downloadFiles,
      gguf_files: ggufFiles,
      download_options: Array.isArray(payload.download_options) ? payload.download_options : [],
      default_download: payload.default_download || '',
      has_files: hasFiles || Boolean(payload.has_files),
      file_count: payload.file_count ?? files.length,
      has_gguf: Boolean(payload.has_gguf) || ggufFiles.length > 0,
      gguf_count: payload.gguf_count ?? ggufFiles.length,
      detail_partial: hasFiles ? false : Boolean(selectedDetail?.detail_partial),
      detail_pending: !hasFiles && Boolean(payload.pending),
      files_error: hasFiles ? '' : String(payload.error || selectedDetail?.files_error || ''),
    };
    selectedDetail = next;
    if (hasFiles) {
      const cacheKey = detailCacheKey(repoId, currentCategory());
      const cached = detailCache.get(cacheKey);
      detailCache.set(cacheKey, { ...(cached || next), ...next, detail_partial: false });
      if (mergeCatalogListDetail(repoId, next)) persistCurrentListMetadata();
      renderList();
    }
    renderDetail(selectedDetail);
  }

  function fillFiles(repoId, attempt = 0) {
    if (!repoId || attempt > FILES_POLL_MAX_ATTEMPTS) {
      if (selectedId === repoId && selectedDetail && !listRowHasDetail(selectedDetail)) {
        selectedDetail = {
          ...selectedDetail,
          detail_pending: false,
          detail_partial: false,
          files_error: 'Hugging Face is still slow. Try again in a minute or open the model on Hugging Face.',
        };
        renderDetail(selectedDetail);
      }
      return Promise.resolve();
    }
    if (selectedId === repoId && listRowHasDetail(selectedDetail)) return Promise.resolve();
    const existing = filesInflight.get(repoId);
    if (existing && attempt === 0) return existing;
    const category = currentCategory();
    const req = api(
      `/api/hf/files?repo_id=${encodeURIComponent(repoId)}&category=${encodeURIComponent(category)}`,
      { timeoutMs: 110000 },
    ).then((data) => {
      const files = data?.download_files || data?.gguf_files || [];
      if (Array.isArray(files) && files.length) {
        applyFilesToSelection(repoId, data);
        return;
      }
      if (data?.pending && attempt < FILES_POLL_MAX_ATTEMPTS) {
        if (selectedId === repoId && selectedDetail) {
          selectedDetail = { ...selectedDetail, detail_pending: true, detail_partial: true, files_error: '' };
          renderDetail(selectedDetail);
        }
        window.setTimeout(() => { void fillFiles(repoId, attempt + 1); }, FILES_POLL_DELAY_MS);
        return;
      }
      if (selectedId === repoId && selectedDetail) {
        selectedDetail = {
          ...selectedDetail,
          detail_pending: false,
          detail_partial: false,
          files_error: data?.error
            ? String(data.error)
            : 'No downloadable files listed on Hugging Face for this repo.',
        };
        renderDetail(selectedDetail);
      }
    }).catch(() => {
      if (attempt < FILES_POLL_MAX_ATTEMPTS) {
        window.setTimeout(() => { void fillFiles(repoId, attempt + 1); }, FILES_POLL_DELAY_MS);
        return;
      }
      if (selectedId === repoId && selectedDetail) {
        selectedDetail = {
          ...selectedDetail,
          detail_pending: false,
          detail_partial: false,
          files_error: 'Could not load files from Hugging Face. Check your connection and try again.',
        };
        renderDetail(selectedDetail);
      }
    }).finally(() => {
      if (filesInflight.get(repoId) === req) filesInflight.delete(repoId);
    });
    filesInflight.set(repoId, req);
    return req;
  }

  function mergeCatalogListDetail(repoId, detail) {
    const row = models.find((model) => model.id === repoId);
    if (!row || !detail) return false;
    const fields = [
      'size_gb',
      'size_label',
      'size_bytes',
      'accelerator_only',
      'catalog_draft_primary',
      'has_gguf',
      'gguf_count',
      'file_count',
      'has_files',
      'local_ready',
      'local_auxiliary_only',
      'local_installs',
      'catalog_ready_to_load',
      'runnable',
      'download_files',
      'download_options',
      'gguf_files',
      'tags',
      'fits_machine',
      'fits_machine_uncertain',
      'fits_machine_reason',
      'best_fit_quant_gb',
      'smallest_quant_gb',
      'fits_budget_gb',
      'quant_options_gb',
    ];
    let changed = false;
    fields.forEach((field) => {
      if (detail[field] === undefined || row[field] === detail[field]) return;
      row[field] = detail[field];
      changed = true;
    });
    const preferred = preferredCatalogOption(row);
    const quantSize = formatCatalogFileSize(preferred);
    if (quantSize) {
      const gb = Number(preferred.size_gb);
      if (Number.isFinite(gb) && gb > 0 && row.size_gb !== gb) {
        row.size_gb = gb;
        changed = true;
      }
      if (row.size_label !== quantSize) {
        row.size_label = quantSize;
        changed = true;
      }
    }
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

  async function warmListSizes(rows) {
    const candidates = (rows || [])
      .filter((model) => model?.id && listSizeLabel(model) === '—')
      .filter((model, index, list) => list.findIndex((row) => row.id === model.id) === index)
      .slice(0, LIST_DETAIL_WARM_LIMIT);
    if (!candidates.length) return;
    try {
      const data = await api(
        `/api/hf/repo-sizes?ids=${encodeURIComponent(candidates.map((model) => model.id).join(','))}`,
        { timeoutMs: 20000 },
      );
      const sizes = data.sizes || {};
      let changed = false;
      candidates.forEach((model) => {
        const size = sizes[model.id];
        if (size && mergeCatalogListDetail(model.id, size)) changed = true;
      });
      if (changed) {
        persistCurrentListMetadata();
        putCachedSearch(searchInput()?.value?.trim() || '', currentSort(), currentCategory(), models);
        scheduleListWarmRender();
      }
    } catch {
      /* size fill is best-effort */
    }
  }

  async function warmListDetails(rows, category) {
    void warmListSizes(rows);
    const selected = (rows || []).find((model) => model.id === selectedId);
    if (!selected?.id || listRowHasDetail(selected)) return;
    const run = ++listDetailWarmGen;
    const detail = await prefetchDetail(selected.id, category);
    if (run !== listDetailWarmGen) return;
    if (detail && mergeCatalogListDetail(selected.id, detail)) {
      persistCurrentListMetadata();
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
      const cached = getCachedSearch(query, sort, category);
      if (cached?.models?.length) {
        models = cached.models;
        catalogPrimed = true;
        if (document.body.dataset.activeView === 'catalog' && !searchInput()?.value?.trim()) {
          populateCatalogFilters();
          renderList();
          restoreVisibleSelection({ preferCache: true });
        }
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
              populateCatalogFilters();
              renderList();
              restoreVisibleSelection({ preferCache: true });
            }
          })
          .catch(() => {});
        return;
      }
      const data = await searchCatalog('', sort, category);
      const rows = data.models || [];
      putCachedSearch(query, sort, category, rows);
      catalogPrimed = true;
      if (rows[0]?.id) void prefetchDetail(rows[0].id, category);
    } catch {
      /* prefetch is best-effort */
    }
  }

  async function searchCatalog(query, sort, category) {
    const path = `/api/hf/search?q=${encodeURIComponent(query)}&sort=${encodeURIComponent(sort)}&category=${encodeURIComponent(category)}&limit=25`;
    const textQuery = String(query || '').trim();
    const timeoutMs = textQuery
      ? 12000
      : (category === 'supported' || category === 'all-gguf' ? 60000 : 35000);
    return api(path, { timeoutMs });
  }

  function renderListLoading(message) {
    const category = currentCategory();
    const query = searchInput()?.value?.trim() || '';
    if (message) {
      const list = searchList();
      if (list) list.innerHTML = `<div class="lm-search-empty df-catalog-loading-message">${escapeHtml(message)}</div>`;
      return;
    }
    renderCatalogLoading('list', loadingCopy(category, query));
  }

  function renderList() {
    const list = searchList();
    const category = currentCategory();
    const visible = visibleModels();
    if (!visible.length) {
      const lab = currentCreator();
      const uploader = currentUploader();
      const hint = installedOnly()
        ? 'No installed models in this search. Try another query or pick a different filter.'
        : fitsMachineOnly()
        ? 'No models in this search fit your GPU VRAM. Try another query or clear the filter.'
        : lab && uploader
        ? `No ${lab} models from ${uploader} in this search. Try another filter or clear Lab / Provider.`
        : lab
        ? `No ${lab} models in this search. Try another lab or clear the filter.`
        : uploader
        ? `No models from ${uploader} in this search. Try another provider or clear the filter.`
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
      const rec = model.catalog_recommended ? ' catalog-recommended' : '';
      return `
        <button type="button" class="lm-search-item${selected}${ready}${rec}" data-repo-id="${escapeHtml(model.id)}">
          ${avatarImg(model.author, model.author_avatar_url, 'lm-hf-avatar sm')}
          <div class="lm-search-item-main">
            <div class="lm-search-item-title-row">
              <span class="lm-search-item-name">${escapeHtml(modelTitle(model))}</span>
              ${listBadges ? `<div class="lm-search-item-badge-slot">${listBadges}</div>` : ''}
            </div>
            ${descLine}
            ${metaLine}
            ${catalogListNotRunnableNote(model)}
          </div>
          <div class="lm-search-item-aside">
            <div class="lm-search-item-stats">
              <span class="lm-search-item-stat lm-search-item-stat-age" title="Hugging Face last update">${escapeHtml(listAgeLabel(model))}</span>
              <span class="lm-search-item-stat lm-search-item-stat-disk" title="Approximate downloadable model size on disk">${escapeHtml(listDiskLabel(model))}</span>
            </div>
            ${catalogRecommendedBadge(model)}
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
    if (!pick) return '';
    const value = String(pick.value || '').trim();
    if (value) return value;
    const active = pick.closest('.df-select-wrap')?.querySelector('.df-select-option.active');
    return String(active?.dataset?.value || '').trim();
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

  async function hydrateLocalInstalls(model) {
    if (!model?.id) return;
    if (!model.local_installs) model.local_installs = {};
    const fromDetail = model.local_installs;
    if (fromDetail && typeof fromDetail === 'object') {
      Object.entries(fromDetail).forEach(([key, rows]) => {
        if (Array.isArray(rows) && rows.length) model.local_installs[key] = rows;
      });
    }
    const files = model.download_files || model.gguf_files || [];
    if (files.length <= 32) {
      for (const file of files) {
        const name = String(file?.filename || '').trim();
        if (!name || model.local_installs[name]?.length) continue;
        try {
          const data = await api(
            `/api/hf/local-match?repo_id=${encodeURIComponent(model.id)}&filename=${encodeURIComponent(name)}`,
          );
          const matches = data?.matches || [];
          if (matches.length) model.local_installs[name] = matches;
        } catch {
          /* best effort */
        }
      }
    }
    try {
      const data = await api(`/api/hf/local-installs?repo_id=${encodeURIComponent(model.id)}`);
      (data?.matches || []).forEach((match) => {
        const key = String(match?.filename || '').trim();
        if (key) model.local_installs[key] = [match];
      });
    } catch {
      /* best effort */
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
    const selectedIsAuxiliary = isAuxiliaryCatalogFilename(filename);
    const weightInstall = install && !selectedIsAuxiliary ? install : null;
    const repoInstall = weightInstall
      || (!filename && model.local_ready ? await resolveAnyLocalInstall(model) : null);
    const auxiliaryOnly = selectedIsAuxiliary && Boolean(install);
    const installed = Boolean(repoInstall) && !auxiliaryOnly;
    const stackReady = catalogReadyToLoad(model);
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
      card?.classList.toggle('ready-to-load', stackReady);
    } else if (auxiliaryOnly) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Download';
        btn.dataset.action = 'download';
      }
      saveNote?.classList.remove('hidden');
      if (installedNote) {
        installedNote.classList.remove('hidden');
        installedNote.innerHTML = `
          <span class="df-catalog-installed-label">Calibration file only</span>
          <code class="df-catalog-installed-path">${escapeHtml(install?.path || '')}</code>
          <span class="df-catalog-installed-hint">Pick a model quant above to download the weights.</span>`;
      }
      card?.classList.remove('ready-to-load');
    } else if (stackReady) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Download';
        btn.dataset.action = 'download';
      }
      saveNote?.classList.remove('hidden');
      installedNote?.classList.add('hidden');
      if (installedNote) installedNote.innerHTML = '';
      card?.classList.add('ready-to-load');
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

  function downloadPctDisplay(job) {
    const queue = window.DFlashDownloadQueue;
    const width = queue?.progressWidth?.(job);
    if (width != null) return `${Math.round(width)}%`;
    if (job?.retrying) return '…';
    return '…';
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

    const width = queue?.progressWidth?.(job);
    const indeterminate = job.status === 'downloading' && width == null;

    progressEl?.classList.remove('hidden');
    if (labelEl) {
      labelEl.textContent = `Downloading ${job.filename || modelTitle(model)}`;
    }
    if (pctEl) {
      pctEl.textContent = downloadPctDisplay(job);
    }
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
      const speed = queue?.formatSpeed?.(job.speed_bps) || '';
      const eta = queue?.formatEta?.(job.eta_seconds) || '';
      const parts = [`Downloading to ${job.path || 'models folder'}`, bytes, speed, eta].filter(Boolean);
      statusEl.textContent = parts.join(' · ');
      statusEl.classList.remove('hidden');
    }

    if (job.status === 'done') {
      hideCardDownloadProgress();
      if (btn) buttonTools?.appendChild(btn);
      if (statusEl) {
        statusEl.textContent = `Saved to ${job.path || 'models folder'}`;
        statusEl.classList.remove('hidden');
      }
      if (window.DFlashModelsLive?.refresh) void window.DFlashModelsLive.refresh();
      void refreshInstallUI(model);
    } else if (job.status === 'error') {
      hideCardDownloadProgress();
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
    const downloadOptions = catalogDownloadOptions(model);
    const filesPending = !downloadOptions.length && (model.detail_pending || model.detail_partial);
    const defaultFn = String(model.default_download || '').trim();
    let selectedOptionIdx = downloadOptions.findIndex(
      (opt) => opt.filename === defaultFn && !isAuxiliaryCatalogFilename(opt.filename),
    );
    if (selectedOptionIdx < 0) {
      selectedOptionIdx = downloadOptions.findIndex((opt) => !isAuxiliaryCatalogFilename(opt.filename));
    }
    if (selectedOptionIdx < 0) selectedOptionIdx = 0;
    const fileOptions = downloadOptions.map((opt, idx) => {
      const name = String(opt.filename || '').trim();
      const label = catalogDownloadOptionLabel(opt);
      return `<option value="${escapeHtml(name)}" title="${escapeHtml(label)}"${idx === selectedOptionIdx ? ' selected' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
    const defaultOpt = downloadOptions[selectedOptionIdx] || downloadOptions[0];
    const initialSize = formatCatalogFileSize(defaultOpt);
    const fieldLabel = catalogDownloadFieldLabel(downloadOptions);
    const downloadHint = catalogDownloadHint(model, downloadOptions);
    const fileSizeEl = downloadOptions.length
      ? `<span class="df-catalog-file-size${initialSize ? '' : ' hidden'}" id="hfSelectedFileSize" title="${initialSize ? `Total download size: ${escapeHtml(initialSize)}` : ''}">${escapeHtml(initialSize)}</span>`
      : '';
    const filePick = downloadOptions.length
      ? `<div class="df-catalog-file-row">
          <label class="df-catalog-field-label" for="hfFilePick">${escapeHtml(fieldLabel)}</label>
          <select class="lm-select small df-catalog-file-select" id="hfFilePick">${fileOptions}</select>
        </div>`
      : (filesPending
        ? `<div class="df-catalog-file-row">
          <label class="df-catalog-field-label" for="hfFilePick">Files</label>
          <select class="lm-select small df-catalog-file-select" id="hfFilePick" disabled><option>Loading files…</option></select>
        </div>`
        : '');
    const shardedHint = downloadHint
      ? `<p class="lm-setting-desc df-catalog-download-hint">${escapeHtml(downloadHint)}</p>`
      : '';
    const downloadBtnLabel = initialSize ? `↓ Download (${initialSize})` : '↓ Download';
    const downloadBtn = downloadOptions.length
      ? `<button class="lm-btn hf-primary hf-download-btn" type="button" id="hfDownloadBtn" data-action="download" title="Download from Hugging Face">${escapeHtml(downloadBtnLabel)}</button>`
      : (filesPending
        ? '<button class="lm-btn hf-primary hf-download-btn" type="button" disabled>Fetching files…</button>'
        : '');
    const savePath = downloadTargetLabel(downloadLibraryId);
    const downloadNote = downloadOptions.length
      ? `${shardedHint}<p class="lm-gpu-ok lm-search-save-path" id="hfSaveNote">New downloads save to <code>${escapeHtml(savePath)}</code></p>
         <div class="df-catalog-installed-note hidden" id="hfInstalledNote"></div>`
      : filesPending
        ? '<p class="lm-setting-desc">Fetching the file list from Hugging Face…</p>'
        : (model.files_error
          ? `<p class="lm-setting-desc">${escapeHtml(model.files_error)}</p>`
          : '<p class="lm-setting-desc">No downloadable files listed on Hugging Face for this repo.</p>');
    const downloadStatus = downloadOptions.length
      ? '<p class="lm-search-download-status hidden" id="hfDownloadStatus"></p>'
      : '';

    const summaryText = textOnlyDescription(modelDescription(model));
    const summaryHtml = summaryText
      ? `<p class="lm-search-description">${renderDescriptionHtml(summaryText)}</p>`
      : (!String(model.readme || '').trim() && (model.detail_partial || model.readme_pending)
        ? '<p class="lm-search-description">Fetching description from Hugging Face…</p>'
        : '');
    const readmePending = !String(model.readme || '').trim()
      && (model.readme_pending || model.detail_pending || model.detail_partial);
    const readmeHtml = readmePending
      ? '<p class="lm-readme-empty">Fetching README from Hugging Face…</p>'
      : renderReadmeContent(model.readme, model.id);
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
              ${summaryHtml}
              <div class="df-catalog-quant-download-row">
                ${filePick}
                ${fileSizeEl}
                <div class="df-catalog-quant-download-actions">
                  ${downloadBtn}
                </div>
                <div class="df-catalog-download-progress hidden" id="hfDownloadProgress">
                  <div class="df-catalog-download-progress-head">
                    <span id="hfDownloadProgressLabel">Downloading…</span>
                    <span class="df-catalog-download-progress-pct" id="hfDownloadProgressPct">0%</span>
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
          <div class="lm-readme-body">${readmeHtml}</div>
        </section>
      </div>`;

    document.getElementById('hfCopyRepo')?.addEventListener('click', () => {
      navigator.clipboard.writeText(model.id).then(() => toast('Repo id copied'));
    });
    bindReadmeCopyButtons(pane);
    document.getElementById('hfCreateStackBtn')?.addEventListener('click', async () => {
      const filename = getSelectedFilename();
      if (!filename) {
        toast('Pick a GGUF file first', false);
        return;
      }
      const install = await resolveLocalInstall(model, filename);
      if (isAuxiliaryCatalogFilename(filename)) {
        toast('Pick a model quant file — calibration files are not loadable weights', false);
        return;
      }
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
      updateSelectedFileSize(model);
      const opt = selectedDownloadOption(model);
      const btn = document.getElementById('hfDownloadBtn');
      const size = formatCatalogFileSize(opt);
      if (btn && size) btn.textContent = `↓ Download (${size})`;
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
    updateSelectedFileSize(model);
    window.DFlashSelectTheme?.enhanceAll?.(document.getElementById('hfSearchDetail'));
    void hydrateLocalInstalls(model).then(() => refreshInstallUI(model));
  }

  function renderDetailPlaceholder(message) {
    const pane = detailPane();
    if (!pane) return;
    pane.innerHTML = `<div class="lm-search-placeholder"><p>${escapeHtml(message || 'Select a model to view details, README, and download GGUF files.')}</p></div>`;
  }

  async function runSearch({ background = false } = {}) {
    const rawQuery = searchInput()?.value?.trim() || '';
    const query = normalizeHfSearchQuery(rawQuery);
    const sort = currentSort();
    const category = currentCategory();
    const copy = loadingCopy(category, query);
    const cached = getCachedSearch(query, sort, category);
    const canShowCached = !!(cached?.models?.length);
    const gen = ++searchRefreshGen;

    if (canShowCached && !background) {
      models = cached.models;
      populateCatalogFilters();
      renderList();
      void warmListDetails(visibleModels(), category);
      restoreVisibleSelection({ preferCache: true });
      background = true;
    } else if (!background) {
      renderCatalogLoading('both', copy);
      if (isRepoIdQuery(query)) {
        void requestDetail(query, category)
          .then((model) => {
            if (gen !== searchRefreshGen || !model) return;
            models = [model];
            selectedId = query;
            selectedDetail = model;
            putCachedSearch(query, sort, category, models);
            populateCatalogFilters();
            renderList();
            renderDetail(model);
          })
          .catch(() => {});
      }
    }

    if (background) setListRefreshIndicator(true);

    try {
      const data = await searchCatalog(query, sort, category);
      if (gen !== searchRefreshGen) return;
      models = data.models || [];
      putCachedSearch(query, sort, category, models);
      populateCatalogFilters();
      if (!models.length || !models.some((m) => m.id === selectedId)) {
        selectedId = '';
        selectedDetail = null;
        if (!models.length) {
          renderDetailPlaceholder('No models found. Try the Hugging Face repo id (org/name) without the filename.');
        } else if (!background) {
          renderDetailPlaceholder();
        }
      }
      renderList();
      void warmListDetails(visibleModels(), category);
      const visible = visibleModels();
      if (!visible.length) {
        selectedId = '';
        selectedDetail = null;
        renderDetailPlaceholder('No models found. Try the Hugging Face repo id (org/name) without the filename.');
      } else if (!selectedId && visible[0]) {
        void selectModel(visible[0].id, { preferCache: background, backgroundDetail: background });
      } else if (selectedId && !visible.some((model) => model.id === selectedId)) {
        void selectModel(visible[0].id, { preferCache: background, backgroundDetail: background });
      }
    } catch (err) {
      if (gen !== searchRefreshGen) return;
      if (canShowCached) return;
      models = [];
      const message = /not found|404/i.test(err.message)
        ? 'Hugging Face search is unavailable. Restart DFlash Console, then try again.'
        : /timeout|timed out|huggingface_timeout|unavailable/i.test(err.message)
        ? 'Hugging Face is slow or unreachable. Check your internet connection, then try again or use org/repo (e.g. bartowski/Qwen3.8-27B-GGUF).'
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

  function listRowHasDetail(model) {
    if (!model) return false;
    const files = model.download_files || model.gguf_files || [];
    return Array.isArray(files) && files.length > 0;
  }

  function mergeCatalogDetail(base, incoming) {
    const merged = { ...(base || {}), ...(incoming || {}) };
    if (!listRowHasDetail(incoming) && listRowHasDetail(base)) {
      [
        'download_files',
        'gguf_files',
        'download_options',
        'default_download',
        'has_files',
        'file_count',
        'has_gguf',
        'gguf_count',
      ].forEach((field) => {
        if (base[field] !== undefined) merged[field] = base[field];
      });
    }
    if (listRowHasDetail(merged)) {
      merged.detail_partial = false;
      merged.detail_pending = false;
      merged.files_error = '';
    }
    return merged;
  }

  async function selectModel(repoId, { preferCache = false, backgroundDetail = false } = {}) {
    if (!repoId) return;
    selectedId = repoId;
    renderList();
    const category = currentCategory();
    const cacheKey = detailCacheKey(repoId, category);
    const cachedDetail = detailCache.get(cacheKey);
    const listRow = models.find((model) => model.id === repoId);
    if (preferCache && cachedDetail && !cachedDetail.detail_partial) {
      selectedDetail = cachedDetail;
      renderDetail(selectedDetail);
      if (!String(cachedDetail.readme || '').trim()) void fillReadme(repoId);
      if (!listRowHasDetail(cachedDetail)) void fillFiles(repoId);
      if (backgroundDetail) void refreshDetail(repoId, category, { silent: true });
      return;
    }
    if (listRow) {
      selectedDetail = {
        ...listRow,
        detail_pending: !listRowHasDetail(listRow),
        readme_pending: !String(listRow.readme || '').trim(),
      };
      renderDetail(selectedDetail);
      void refreshDetail(repoId, category, { silent: true });
      if (!String(listRow.readme || '').trim()) void fillReadme(repoId);
      if (!listRowHasDetail(listRow)) void fillFiles(repoId);
      return;
    }
    renderDetailLoading();
    void refreshDetail(repoId, category, { silent: backgroundDetail });
  }

  async function refreshDetail(repoId, category, { silent = false } = {}) {
    try {
      const model = await requestDetail(repoId, category);
      const listRow = models.find((row) => row.id === repoId);
      if (mergeCatalogListDetail(repoId, model)) {
        persistCurrentListMetadata();
        renderList();
      }
      if (selectedId === repoId) {
        const base = { ...(listRow || {}) };
        if (selectedDetail?.id === repoId) Object.assign(base, selectedDetail);
        const merged = mergeCatalogDetail(base, model);
        const readmeReady = Boolean(String(merged.readme || '').trim());
        selectedDetail = {
          ...merged,
          detail_pending: !listRowHasDetail(merged) && Boolean(merged.detail_partial),
          readme_pending: !readmeReady && Boolean(merged.readme_pending || merged.detail_partial),
        };
        renderDetail(selectedDetail);
        if (!readmeReady) void fillReadme(repoId);
        if (!listRowHasDetail(merged)) void fillFiles(repoId);
      }
    } catch (err) {
      if (selectedId !== repoId) return;
      const listRow = models.find((model) => model.id === repoId);
      const fallback = (selectedDetail && selectedDetail.id === repoId) ? selectedDetail : listRow;
      if (fallback) {
        selectedDetail = { ...fallback, detail_pending: !listRowHasDetail(fallback) };
        renderDetail(selectedDetail);
        if (!listRowHasDetail(fallback)) void fillFiles(repoId);
        if (!silent) toast(err.message, false);
        return;
      }
      renderDetailPlaceholder(err.message);
      if (!silent) toast(err.message, false);
    }
  }

  async function startDownload(repoId, filename, libraryId, model) {
    const install = model ? await resolveLocalInstall(model, filename) : null;
    if (install && !isAuxiliaryCatalogFilename(filename)) {
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
        meta: model ? {
          lab: modelLab(model),
          author: modelUploader(model),
          quant: String(model.quant || '').trim(),
          size_label: String(model.size_label || '').trim() || (model.size_gb ? `${model.size_gb} GB` : ''),
          pipeline_tag: model.pipeline_tag || '',
          modality: String(model.modality || '').trim(),
          accelerator_only: model.accelerator_only === true,
          reasoning: model.reasoning === true,
          capabilities: Array.isArray(model.capabilities) ? model.capabilities : [],
          dflash_generation_label: String(model.dflash_generation_label || '').trim(),
          format: filename.toLowerCase().endsWith('.gguf') ? 'GGUF' : '',
        } : {
          author: repoId.includes('/') ? repoId.split('/')[0] : repoId,
          format: filename.toLowerCase().endsWith('.gguf') ? 'GGUF' : '',
        },
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
    populateCatalogFilters();
    const input = searchInput();
    input?.focus();

    const query = input?.value?.trim() || '';
    const sort = currentSort();
    const category = currentCategory();
    const cached = getCachedSearch(query, sort, category);
    if (cached?.models?.length) {
      models = cached.models;
      populateCatalogFilters();
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

  function onUploaderFilterChange() {
    onListFilterChange();
  }

  function bind() {
    searchInput()?.addEventListener('input', scheduleSearch);
    document.getElementById('hfSearchSort')?.addEventListener('change', () => {
      if (installedOnly() || fitsMachineOnly() || acceleratorsOnly()) onListFilterChange();
      else void runSearch();
    });
    document.getElementById('hfSearchCategory')?.addEventListener('change', () => void runSearch());
    document.getElementById('hfSearchCreator')?.addEventListener('change', onCreatorFilterChange);
    document.getElementById('hfSearchUploader')?.addEventListener('change', onUploaderFilterChange);
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
    populateCatalogFilters();
    const cached = getCachedSearch('', 'downloads', DEFAULT_CATEGORY);
    if (cached?.models?.length) {
      models = cached.models;
      catalogPrimed = true;
      populateCatalogFilters();
    }
    void warmCatalogCache();
    window.setInterval(() => {
      catalogPrimed = false;
      void warmCatalogCache();
    }, CATALOG_REFRESH_MS);
  });

  window.DFlashModelSearchLive = { onViewEnter, runSearch, warmCatalogCache, revealRepo: selectModel };
})();
