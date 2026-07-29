/** Library scan modal — discover model folders on PC */
(function () {
  const { api, toast } = window.StudioApi;

  let scanPreset = 'dflash';
  let candidates = [];
  let selectedPaths = new Set();

  const PRESET_OPTIONS = window.DFlashLibraryTypes?.options || [
    { id: 'dflash', label: 'DFlash' },
    { id: 'gguf', label: 'GGUF checkpoints' },
    { id: 'speech', label: 'Speech-to-text' },
    { id: 'tts', label: 'Text-to-speech' },
    { id: 'ocr', label: 'OCR' },
    { id: 'embeddings', label: 'Embeddings' },
    { id: 'custom', label: 'All model types' },
  ];

  const PRESET_TITLES = Object.fromEntries(PRESET_OPTIONS.map((row) => [row.id, row.label]));

  const TYPE_LABELS = {
    gguf: 'GGUF',
    piper: 'Piper TTS',
    whisper: 'Whisper STT',
    hub: 'Hugging Face cache',
    ocr: 'OCR',
    embeddings: 'Embeddings',
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function modal() {
    return document.getElementById('libraryScanModal');
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
    const select = document.getElementById('libraryScanPreset');
    if (!select) return;
    select.innerHTML = PRESET_OPTIONS.map((row) =>
      `<option value="${escapeHtml(row.id)}"${row.id === selectedId ? ' selected' : ''}>${escapeHtml(row.label)}</option>`,
    ).join('');
  }

  function updateScanTitle() {
    const title = document.getElementById('libraryScanTitle');
    if (title) title.textContent = `Scan · ${PRESET_TITLES[scanPreset] || 'Models'}`;
  }

  function filteredCandidates(filterText = '') {
    const needle = String(filterText || '').trim().toLowerCase();
    return candidates.filter((row) => {
      if (!needle) return true;
      const hay = [row.path, row.label, ...(row.sample_models || [])].join(' ').toLowerCase();
      return hay.includes(needle);
    });
  }

  function updateAddButton() {
    const btn = document.getElementById('libraryScanAddSelected');
    if (btn) btn.disabled = selectedPaths.size === 0;
  }

  function updateSelectAllCheckbox(filterText = '') {
    const selectAll = document.getElementById('libraryScanSelectAll');
    if (!selectAll) return;
    const rows = filteredCandidates(filterText);
    if (!rows.length) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
      return;
    }
    const allSelected = rows.every((row) => selectedPaths.has(row.path));
    const someSelected = rows.some((row) => selectedPaths.has(row.path));
    selectAll.checked = allSelected;
    selectAll.indeterminate = !allSelected && someSelected;
  }

  function renderResults(filterText = '') {
    const wrap = document.getElementById('libraryScanResults');
    const meta = document.getElementById('libraryScanMeta');
    if (!wrap) return;
    const rows = filteredCandidates(filterText);
    if (meta) {
      meta.textContent = rows.length
        ? `${selectedPaths.size} selected · ${rows.length} location${rows.length === 1 ? '' : 's'} found`
        : 'No matching folders';
    }
    if (!rows.length) {
      wrap.innerHTML = '<div class="lm-search-empty">No model folders found. Try another library type or add a custom folder path.</div>';
      updateAddButton();
      updateSelectAllCheckbox(filterText);
      return;
    }
    wrap.innerHTML = rows.map((row) => {
      const checked = selectedPaths.has(row.path) ? ' checked' : '';
      const type = TYPE_LABELS[row.model_type] || row.model_type || 'Models';
      const samples = (row.sample_models || []).slice(0, 3).map((s) => `<span class="lm-tag dim">${escapeHtml(s)}</span>`).join('');
      const size = row.size_gb != null && row.size_gb > 0 ? `${row.size_gb} GB · ` : '';
      return `
        <label class="lm-scan-card${checked ? ' selected' : ''}">
          <input type="checkbox" class="lm-scan-check" data-scan-path="${escapeHtml(row.path)}"${checked}>
          <div class="lm-scan-card-body">
            <div class="lm-scan-card-top">
              <strong>${escapeHtml(row.label || row.path)}</strong>
              <span class="lm-tag">${escapeHtml(type)}</span>
            </div>
            <code class="lm-settings-path">${escapeHtml(row.path)}</code>
            <div class="lm-scan-card-meta">${escapeHtml(size)}${escapeHtml(row.model_count || 0)} models</div>
            <div class="lm-scan-card-samples">${samples}</div>
          </div>
        </label>`;
    }).join('');

    wrap.querySelectorAll('.lm-scan-check').forEach((input) => {
      input.addEventListener('change', () => {
        const path = input.dataset.scanPath;
        if (input.checked) selectedPaths.add(path);
        else selectedPaths.delete(path);
        input.closest('.lm-scan-card')?.classList.toggle('selected', input.checked);
        updateAddButton();
        updateSelectAllCheckbox(filterText);
        if (meta) {
          meta.textContent = `${selectedPaths.size} selected · ${rows.length} location${rows.length === 1 ? '' : 's'} found`;
        }
      });
    });
    updateAddButton();
    updateSelectAllCheckbox(filterText);
  }

  async function runScan() {
    const results = document.getElementById('libraryScanResults');
    const subtitle = document.getElementById('libraryScanSubtitle');
    const selectAll = document.getElementById('libraryScanSelectAll');
    if (selectAll) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
    }
    if (results) results.innerHTML = '<div class="lm-search-empty">Scanning this PC for model folders…</div>';
    if (subtitle) subtitle.textContent = 'Searching common install locations, caches, and user folders…';
    try {
      const data = await api(`/api/model-libraries/scan?preset=${encodeURIComponent(scanPreset)}`);
      candidates = data.candidates || [];
      selectedPaths = new Set();
      if (subtitle) {
        subtitle.textContent = candidates.length
          ? `Found ${candidates.length} location${candidates.length === 1 ? '' : 's'} for ${PRESET_TITLES[scanPreset] || scanPreset}`
          : `No ${PRESET_TITLES[scanPreset] || scanPreset} folders detected on this PC yet`;
      }
      const meta = document.getElementById('libraryScanMeta');
      if (meta && data.elapsed_ms != null) {
        meta.textContent = `Scanned ${data.scanned_dirs || 0} folders in ${Math.max(1, Math.round(data.elapsed_ms / 100) / 10)}s`;
      }
      renderResults(document.getElementById('libraryScanFilter')?.value || '');
    } catch (err) {
      if (results) results.innerHTML = `<div class="lm-search-empty">${escapeHtml(err.message)}</div>`;
      toast(err.message, false);
    }
  }

  function openScan(preset) {
    scanPreset = preset || 'dflash';
    selectedPaths = new Set();
    candidates = [];
    populatePresetSelect(scanPreset);
    updateScanTitle();
    openModal();
    void runScan();
  }

  function selectedCandidates() {
    return candidates.filter((row) => selectedPaths.has(row.path));
  }

  function bind() {
    populatePresetSelect(scanPreset);
    document.getElementById('libraryScanPreset')?.addEventListener('change', (e) => {
      scanPreset = e.target.value || 'dflash';
      selectedPaths = new Set();
      const filter = document.getElementById('libraryScanFilter');
      if (filter) filter.value = '';
      updateScanTitle();
      void runScan();
    });
    document.getElementById('libraryScanSelectAll')?.addEventListener('change', (e) => {
      const filter = document.getElementById('libraryScanFilter')?.value || '';
      const rows = filteredCandidates(filter);
      if (e.target.checked) {
        rows.forEach((row) => selectedPaths.add(row.path));
      } else {
        rows.forEach((row) => selectedPaths.delete(row.path));
      }
      renderResults(filter);
    });
    document.getElementById('libraryScanRescan')?.addEventListener('click', () => void runScan());
    document.getElementById('libraryScanFilter')?.addEventListener('input', (e) => {
      renderResults(e.target.value);
    });
    document.getElementById('libraryScanAddSelected')?.addEventListener('click', () => {
      const picked = selectedCandidates();
      if (!picked.length) return;
      if (window.DFlashSettingsLive?.addLibrariesFromScan) {
        window.DFlashSettingsLive.addLibrariesFromScan(picked);
      }
      closeModal();
    });

    modal()?.querySelector('[data-action="close-modal"]')?.addEventListener('click', closeModal);
    modal()?.querySelector('.lm-modal-backdrop')?.addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeModal();
    });
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashLibraryScan = { openScan, closeModal };
})();
