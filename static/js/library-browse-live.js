/** Library browse modal — pick a folder manually */
(function () {
  const { api, toast } = window.ConsoleApi;

  let browsePreset = 'dflash';
  let currentPath = '';
  let parentPath = '';
  let pathLabel = 'This PC';
  let previewData = null;

  const TYPE_OPTIONS = window.DFlashLibraryTypes?.options || [
    { id: 'dflash', label: 'DFlash' },
    { id: 'gguf', label: 'GGUF checkpoints' },
    { id: 'tts', label: 'Text-to-speech' },
    { id: 'speech', label: 'Speech-to-text' },
    { id: 'ocr', label: 'OCR' },
    { id: 'embeddings', label: 'Embeddings' },
    { id: 'custom', label: 'All model types' },
  ];

  const PREVIEW_TYPE_LABELS = {
    gguf: 'GGUF checkpoint',
    piper: 'text-to-speech',
    whisper: 'speech-to-text',
    hub: 'Hugging Face model',
    ocr: 'OCR',
    embeddings: 'embedding',
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function modal() {
    return document.getElementById('libraryBrowseModal');
  }

  function openModal() {
    const el = modal();
    if (!el) return;
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
  }

  function closeModal() {
    const el = modal();
    if (!el) return;
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) {
      document.body.classList.remove('modal-open');
    }
  }

  function populatePresetSelect(selectedId) {
    const select = document.getElementById('libraryBrowsePreset');
    if (!select) return;
    select.innerHTML = TYPE_OPTIONS.map((row) =>
      `<option value="${escapeHtml(row.id)}"${row.id === selectedId ? ' selected' : ''}>${escapeHtml(row.label)}</option>`,
    ).join('');
  }

  function updateSubtitle() {
    const subtitle = document.getElementById('libraryBrowseSubtitle');
    const typeLabel = window.DFlashLibraryTypes?.title?.(browsePreset) || browsePreset;
    if (subtitle) {
      subtitle.textContent = `Model type: ${typeLabel}. Pick any folder on your PC — use drives or quick places below.`;
    }
  }

  function renderRoots(groups) {
    const wrap = document.getElementById('libraryBrowseRoots');
    if (!wrap) return;
    const sections = Array.isArray(groups) ? groups : [];
    if (!sections.length) {
      wrap.innerHTML = '';
      return;
    }
    wrap.innerHTML = sections.map((section) => {
      const items = Array.isArray(section.items) ? section.items : [];
      if (!items.length) return '';
      const buttons = items.map((row) =>
        `<button type="button" class="lm-btn ghost small" data-browse-root="${escapeHtml(row.path)}">${escapeHtml(row.label)}</button>`,
      ).join('');
      return `
        <div class="lm-browse-root-group">
          <div class="lm-browse-root-group-label">${escapeHtml(section.label || '')}</div>
          <div class="lm-browse-root-group-items">${buttons}</div>
        </div>`;
    }).join('');
    wrap.querySelectorAll('[data-browse-root]').forEach((btn) => {
      btn.addEventListener('click', () => {
        void loadDirectory(btn.dataset.browseRoot ?? '');
      });
    });
  }

  function renderPreview() {
    const el = document.getElementById('libraryBrowsePreview');
    if (!el) return;
    const preview = previewData || {};
    const count = preview.model_count != null ? preview.model_count : 0;
    const type = PREVIEW_TYPE_LABELS[preview.model_type] || 'model';
    el.textContent = count
      ? `${count} ${type} file${count === 1 ? '' : 's'} found in this folder`
      : 'No models detected in this folder yet — you can still add it as a library location';
  }

  function entryIcon(row) {
    if (row.kind === 'drive') return '💽';
    return '📁';
  }

  function renderList(entries) {
    const wrap = document.getElementById('libraryBrowseList');
    if (!wrap) return;
    const rows = Array.isArray(entries) ? entries : [];
    if (!rows.length) {
      wrap.innerHTML = '<div class="lm-search-empty">No folders here.</div>';
      return;
    }
    wrap.innerHTML = rows.map((row) =>
      `<button type="button" class="lm-browse-folder" data-browse-enter="${escapeHtml(row.path)}">${entryIcon(row)} ${escapeHtml(row.name)}</button>`,
    ).join('');
    wrap.querySelectorAll('[data-browse-enter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        void loadDirectory(btn.dataset.browseEnter ?? '');
      });
    });
  }

  async function loadPreview() {
    const el = document.getElementById('libraryBrowsePreview');
    if (!currentPath) {
      previewData = null;
      if (el) el.textContent = 'Select a folder to check for models';
      return;
    }
    if (el) el.textContent = 'Checking folder for models…';
    try {
      const query = new URLSearchParams({
        preset: browsePreset,
        path: currentPath,
      });
      const data = await api(`/api/model-libraries/preview?${query.toString()}`);
      previewData = data.library || null;
      renderPreview();
    } catch (err) {
      previewData = null;
      if (el) el.textContent = err.message || 'Could not preview folder';
    }
  }

  async function loadDirectory(path) {
    const list = document.getElementById('libraryBrowseList');
    const pathEl = document.getElementById('libraryBrowsePath');
    const upBtn = document.getElementById('libraryBrowseUp');
    if (list) list.innerHTML = '<div class="lm-search-empty">Loading…</div>';
    try {
      const query = new URLSearchParams({
        preset: browsePreset,
        path: path ?? '',
      });
      const data = await api(`/api/fs/browse?${query.toString()}`);
      currentPath = data.path || '';
      parentPath = data.parent ?? '';
      pathLabel = data.path_label || currentPath || 'This PC';
      previewData = null;
      if (pathEl) pathEl.textContent = pathLabel || 'This PC';
      if (upBtn) upBtn.disabled = !currentPath;
      renderRoots(data.quick_roots);
      renderList(data.entries);
      void loadPreview();
      void updateImportHint();
    } catch (err) {
      if (list) list.innerHTML = `<div class="lm-search-empty">${escapeHtml(err.message)}</div>`;
      toast(err.message, false);
    }
  }

  function getImportMode() {
    return document.querySelector('input[name="libraryImportMode"]:checked')?.value || 'link';
  }

  async function updateImportHint() {
    const hint = document.getElementById('libraryImportHint');
    if (!hint) return;
    const mode = getImportMode();
    if (!currentPath) {
      hint.textContent = mode === 'link'
        ? 'Link keeps files in the original location.'
        : 'Choose a folder to see where models will be placed.';
      return;
    }
    if (mode === 'link') {
      hint.textContent = 'Files stay in the folder you selected. DFlash Console will scan that path.';
      return;
    }
    hint.textContent = mode === 'move'
      ? 'Checking destination… Move removes models from the original folder.'
      : 'Checking destination… Copy uses extra disk space but keeps the original files.';
    try {
      const query = new URLSearchParams({
        path: currentPath,
        preset: browsePreset,
        mode,
      });
      const plan = await api(`/api/model-libraries/import-plan?${query.toString()}`);
      if (plan.already_in_library_home) {
        hint.textContent = 'This folder is already inside your DFlash library home.';
        return;
      }
      const verb = mode === 'move' ? 'Move to' : 'Copy to';
      const size = plan.size_gb ? ` · about ${plan.size_gb} GB` : '';
      const count = plan.file_count || plan.model_count || 0;
      const countLabel = count ? `${count} file${count === 1 ? '' : 's'}${size}` : 'folder contents';
      hint.textContent = `${verb} ${plan.destination_path} (${countLabel})`;
    } catch (err) {
      hint.textContent = err.message || 'Could not plan import';
    }
  }

  function openBrowse(preset) {
    browsePreset = preset || 'dflash';
    currentPath = '';
    parentPath = '';
    pathLabel = 'This PC';
    previewData = null;
    populatePresetSelect(browsePreset);
    updateSubtitle();
    openModal();
    void loadDirectory('');
    void updateImportHint();
  }

  function addCurrentFolder() {
    if (!currentPath) {
      toast('Open a folder first — This PC cannot be added', false);
      return;
    }
    const preview = previewData || {};
    const folderName = currentPath.split(/[/\\]/).filter(Boolean).pop() || currentPath;
    const entry = {
      path: currentPath,
      preset: browsePreset,
      label: preview.label || preview.label_hint || folderName,
      model_count: preview.model_count,
      model_type: preview.model_type,
      sample_models: preview.sample_models,
    };
    const mode = getImportMode();
    void (async () => {
      const ok = await window.DFlashSettingsLive?.importLibraryAndAdd?.(entry, mode);
      if (ok !== false) closeModal();
    })();
  }

  function bind() {
    populatePresetSelect(browsePreset);
    document.getElementById('libraryBrowsePreset')?.addEventListener('change', (e) => {
      browsePreset = e.target.value || 'dflash';
      updateSubtitle();
      void loadPreview();
      void updateImportHint();
    });
    document.querySelectorAll('input[name="libraryImportMode"]').forEach((input) => {
      input.addEventListener('change', () => { void updateImportHint(); });
    });
    document.getElementById('libraryBrowseUp')?.addEventListener('click', () => {
      void loadDirectory(parentPath ?? '');
    });
    document.getElementById('libraryBrowseAdd')?.addEventListener('click', addCurrentFolder);

    modal()?.querySelector('[data-action="close-modal"]')?.addEventListener('click', closeModal);
    modal()?.querySelector('.lm-modal-backdrop')?.addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeModal();
    });
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashLibraryBrowse = { openBrowse, closeModal };
})();
