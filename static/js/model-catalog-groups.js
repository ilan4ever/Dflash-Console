/** Shared model catalog grouping for Console dropdowns (DFlash vs LLM). */
(function () {
  const GROUPS = [
    { id: 'gemma', label: 'Gemma' },
    { id: 'qwen', label: 'Qwen' },
    { id: 'ocr', label: 'OCR' },
    { id: 'embedding', label: 'Embedding' },
    { id: 'dflash', label: 'DFlash' },
    { id: 'other', label: 'Other models' },
  ];

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function isDflashEntry(model) {
    if (!model) return false;
    if (model.source === 'dflash-profile') return true;
    if (model.dflash_stack || model.draft_path) return true;
    if (model.source === 'dflash') return true;
    const caps = Array.isArray(model.capabilities) ? model.capabilities : [];
    if (caps.includes('dflash')) return true;
    if (model.loadable && model.server_id && model.draft_path) return true;
    return false;
  }

  function familyIdFor(model) {
    const text = [
      model?.label, model?.filename, model?.id, model?.path, model?.family,
      ...(Array.isArray(model?.capabilities) ? model.capabilities : []),
    ].join(' ').toLowerCase();
    if (/ocr|chandra|image-to-text/.test(text)) return 'ocr';
    if (/embed|nomic|feature-extraction/.test(text)) return 'embedding';
    if (/gemma/.test(text)) return 'gemma';
    if (/qwen/.test(text)) return 'qwen';
    if (isDflashEntry(model)) return 'dflash';
    return 'other';
  }

  function groupIdFor(model) {
    return familyIdFor(model);
  }

  function familyLabelFor(model) {
    const id = familyIdFor(model);
    const group = GROUPS.find((entry) => entry.id === id);
    return group ? group.label : 'Other models';
  }

  function sourceIdFor(model) {
    return String(model?.source || model?.provider || model?.library_label || model?.library || 'Local').trim() || 'Local';
  }

  function sourceLabelFor(model) {
    return sourceIdFor(model).replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function sourceOptions(models) {
    const seen = new Map();
    for (const model of models || []) {
      const id = sourceIdFor(model);
      if (!seen.has(id)) seen.set(id, sourceLabelFor(model));
    }
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }

  function sortModels(list) {
    return list.slice().sort((a, b) => {
      const aLabel = String(a.label || a.filename || a.id || '');
      const bLabel = String(b.label || b.filename || b.id || '');
      return aLabel.localeCompare(bLabel, undefined, { sensitivity: 'base' });
    });
  }

  function groupCatalogModels(models, { catalogKey } = {}) {
    const keyFn = typeof catalogKey === 'function'
      ? catalogKey
      : (model) => model?.server_id || model?.path || model?.id || '';
    const buckets = Object.fromEntries(GROUPS.map((g) => [g.id, []]));
    const seen = new Set();
    for (const model of models || []) {
      const key = keyFn(model);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      buckets[groupIdFor(model)].push(model);
    }
    for (const group of GROUPS) {
      buckets[group.id] = sortModels(buckets[group.id] || []);
    }
    return { groups: GROUPS, buckets };
  }

  function defaultOptionLabel(model) {
    const parts = [model.label || model.filename || model.id || 'Model'];
    if (model.quant && model.quant !== '—') parts.push(model.quant);
    if (model.size_gb != null) parts.push(`${model.size_gb} GB`);
    if (model.loadable && model.port) parts.push(`port :${model.port}`);
    return parts.join(' · ');
  }

  function renderGroupedSelectOptions(models, {
    catalogKey,
    optionLabel,
    placeholder = 'Select…',
    selectedKey = '',
    groupBySource = false,
    optionClass,
  } = {}) {
    const keyFn = typeof catalogKey === 'function'
      ? catalogKey
      : (model) => model?.server_id || model?.path || model?.id || '';
    const labelFn = typeof optionLabel === 'function' ? optionLabel : defaultOptionLabel;
    const classFn = typeof optionClass === 'function' ? optionClass : () => '';

    function optionHtml(model) {
      const key = keyFn(model);
      const selected = key === selectedKey ? ' selected' : '';
      const cls = classFn(model);
      const classAttr = cls ? ` class="${escapeHtml(cls)}"` : '';
      return `<option value="${escapeHtml(key)}"${selected}${classAttr}>${escapeHtml(labelFn(model))}</option>`;
    }

    const seen = new Set();
    function uniqueModels(list) {
      const out = [];
      for (const model of list || []) {
        const key = keyFn(model);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(model);
      }
      return out;
    }

    if (groupBySource) {
      // Group by provider/source first, keeping the model families under each:
      // e.g. "Lmstudio · Qwen", "Dflash Stack · Gemma", one group below another.
      const buckets = new Map();
      for (const model of uniqueModels(models)) {
        const source = sourceLabelFor(model);
        const family = familyLabelFor(model);
        const bucketKey = `${source}\u0000${family}`;
        if (!buckets.has(bucketKey)) {
          buckets.set(bucketKey, { source, family, models: [] });
        }
        buckets.get(bucketKey).models.push(model);
      }
      const entries = [...buckets.values()]
        .sort((a, b) => a.source.localeCompare(b.source) || a.family.localeCompare(b.family));
      const parts = [`<option value="">${escapeHtml(placeholder)}</option>`];
      for (const entry of entries) {
        entry.models = sortModels(entry.models);
        parts.push(`<optgroup label="${escapeHtml(`${entry.source} · ${entry.family}`)}">`);
        for (const model of entry.models) parts.push(optionHtml(model));
        parts.push('</optgroup>');
      }
      return parts.join('');
    }

    const { groups, buckets } = groupCatalogModels(models, { catalogKey: keyFn });
    const parts = [`<option value="">${escapeHtml(placeholder)}</option>`];
    for (const group of groups) {
      const rows = buckets[group.id] || [];
      if (!rows.length) continue;
      parts.push(`<optgroup label="${escapeHtml(group.label)}">`);
      for (const model of rows) parts.push(optionHtml(model));
      parts.push('</optgroup>');
    }
    return parts.join('');
  }

  window.DFlashModelGroups = {
    GROUPS,
    isDflashEntry,
    groupIdFor,
    familyIdFor,
    familyLabelFor,
    sourceIdFor,
    sourceLabelFor,
    sourceOptions,
    sortModels,
    groupCatalogModels,
    defaultOptionLabel,
    renderGroupedSelectOptions,
  };
})();
