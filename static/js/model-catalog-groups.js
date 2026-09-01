/** Shared model catalog grouping for Console dropdowns (DFlash vs LLM). */
(function () {
  const GROUPS = [
    { id: 'gemma', label: 'Gemma' },
    { id: 'qwen', label: 'Qwen' },
    { id: 'ocr', label: 'OCR' },
    { id: 'embedding', label: 'Embedding' },
    { id: 'dflash', label: 'DFlash 1' },
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
      modelIdentityHaystack(model), model?.family,
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

  // Models registered inside the DFlash Console itself (its own model
  // libraries), as opposed to external models (e.g. LM Studio).
  function isConsoleRegisteredModel(model) {
    const source = String(model?.source || '').trim().toLowerCase();
    return source === 'dflash' || source === 'dflash-profile' || source === 'dflash-stack';
  }

  // External models are grouped per provider: known providers get their own
  // group (e.g. LM Studio); loose/unrecognized files go under "Other models".
  function isHashLabel(value) {
    return /^[0-9a-f]{7,64}$/i.test(String(value || '').trim());
  }

  function isHfFolderModel(model) {
    if (!model) return false;
    const runtime = String(model.runtime_id || '').toLowerCase();
    if (runtime === 'vllm' || runtime === 'transformers') return true;
    if (model.kind === 'dir' && String(model.modality || 'llm') === 'llm') return true;
    const path = String(model.path || '').replace(/\\/g, '/').toLowerCase();
    return path.includes('huggingface') && !path.endsWith('.gguf');
  }

  function externalGroupLabelFor(model) {
    const source = String(model?.source || '').trim().toLowerCase();
    if (source === 'lmstudio') return 'LM Studio';
    if (source === 'ollama') return 'Ollama';
    if (isHfFolderModel(model)) return 'Hugging Face';
    if (!source || source === 'local' || source === 'other' || source === 'unknown' || source === 'library') {
      return 'Other models';
    }
    return sourceLabelFor(model);
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

  function familySortRank(model) {
    const id = familyIdFor(model);
    const idx = GROUPS.findIndex((entry) => entry.id === id);
    return idx >= 0 ? idx : GROUPS.length;
  }

  function modelSortLabel(model, optionLabel) {
    if (typeof optionLabel === 'function') return String(optionLabel(model) || '');
    return String(model.label || model.filename || model.id || '');
  }

  function sortModels(list, { optionLabel } = {}) {
    return list.slice().sort((a, b) => {
      const rankA = familySortRank(a);
      const rankB = familySortRank(b);
      if (rankA !== rankB) return rankA - rankB;
      const aLabel = modelSortLabel(a, optionLabel);
      const bLabel = modelSortLabel(b, optionLabel);
      return aLabel.localeCompare(bLabel, undefined, { sensitivity: 'base', numeric: true });
    });
  }

  function sortModelsAlphabetically(list, { optionLabel } = {}) {
    return list.slice().sort((a, b) => {
      const aLabel = modelSortLabel(a, optionLabel);
      const bLabel = modelSortLabel(b, optionLabel);
      return aLabel.localeCompare(bLabel, undefined, { sensitivity: 'base', numeric: true });
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

  function acceleratorGenerationLabel(model) {
    const label = String(model?.dflash_generation_label || '').trim();
    if (label) return label;
    const name = modelIdentityHaystack(model);
    if (/dflash[-_.]?2|dflash2/.test(name)) return 'DFlash 2';
    if (/dflash|dspark/.test(name)) return 'DFlash 1';
    return '';
  }

  function modelIdentityHaystack(model) {
    return [
      model?.filename,
      model?.label,
      model?.id,
      model?.repo_id,
    ].filter(Boolean).join(' ').toLowerCase();
  }

  const ACCELERATOR_MAX_SIZE_GB = 8;

  function pathLeafName(value) {
    const raw = String(value || '').trim().replace(/[\\/]+$/, '');
    if (!raw) return '';
    const parts = raw.split(/[\\/]/);
    return parts[parts.length - 1] || '';
  }

  function acceleratorFileHaystack(model) {
    const parts = [];
    const filename = String(model?.filename || '').trim();
    const looksLikeFile = /\.gguf$/i.test(filename)
      || /[/\\]/.test(filename)
      || String(model?.kind || '').toLowerCase() === 'dir';
    if (filename && looksLikeFile) parts.push(pathLeafName(filename) || filename);
    // Only the model folder/file name — never the full absolute path.
    // Paths under ".../Dflash-Console/models/..." contain "dflash" and
    // otherwise falsely mark every local model as an accelerator.
    const leafPath = pathLeafName(model?.path);
    if (leafPath) parts.push(leafPath);
    const leafModelPath = pathLeafName(model?.model_path);
    if (leafModelPath) parts.push(leafModelPath);
    return parts.join(' ').toLowerCase();
  }

  function isAcceleratorOnlyModel(model) {
    if (!model) return false;
    if (model.accelerator_only === false) return false;
    if (model.dflash_stack || model.draft_path) return false;
    const caps = Array.isArray(model.capabilities) ? model.capabilities : [];
    if (caps.includes('dflash')) return false;
    const role = String(model.role || '').toLowerCase();
    if (role === 'loaded-model' || role === 'alias' || model.plain_llm || model.is_adhoc) {
      return false;
    }
    const size = Number(model.size_gb);
    if (Number.isFinite(size) && size > ACCELERATOR_MAX_SIZE_GB) return false;
    if (model.accelerator_only === true) return true;
    // Stack aliases / display titles often include "dflash" (the pairing),
    // but only actual draft weight files are accelerators.
    const name = acceleratorFileHaystack(model);
    if (!name.trim()) return false;
    if (/(?:^|[^a-z])draft(?:[^a-z]|$)/.test(name)) return true;
    return /dflash|dspark/.test(name);
  }

  function friendlyStackLabel(model) {
    const raw = String(model?.filename || model?.label || model?.id || 'Model').replace(/\.gguf$/i, '');
    let stem = raw.replace(/(?:^|[\s_\-])(?:Q\d(?:[_A-Z0-9]+)?|IQ\d[_A-Z0-9]+|F16|F32|BF16)(?:$|[\s_\-])/gi, ' ');
    stem = stem.replace(/[_-]+/g, ' ');
    stem = stem.replace(/\b(?:gguf|instruct|chat|it|qat|draft|llama|cpp|ud)\b/gi, ' ');
    stem = stem.replace(/^(qwen|google|bartowski|lmstudio|meta|mistral)\s+/i, (match, org) => (
      /qwen/i.test(org) ? 'Qwen ' : ''
    ));
    stem = stem.replace(/\b(qwen|gemma)\s+\1(?=\d)/gi, '$1');
    stem = stem.replace(/\b(qwen)(\d)/gi, 'Qwen $2');
    stem = stem.replace(/\b(gemma)(\d)/gi, 'Gemma $2');
    stem = stem.replace(/\s+/g, ' ').trim();
    const words = stem.split(' ').filter(Boolean).map((word) => {
      const lower = word.toLowerCase();
      if (['qwen', 'gemma', 'deepseek', 'bonsai', 'laguna'].includes(lower)) {
        return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
      }
      if (/^\d+(?:\.\d+)?[Bb]$/.test(word)) return word.toUpperCase().replace(/b$/i, 'B');
      if (/^[A-Za-z]\d+[A-Za-z]?$/.test(word)) return word.toUpperCase();
      return word;
    });
    const label = words.join(' ').trim() || raw;
    if (!/\bd-?flash\b/i.test(label)) return `${label} D-Flash`;
    return label;
  }

  function stackDisplayName(model) {
    const apiLabel = String(model?.label || '').trim().replace(/\.gguf$/i, '');
    if (apiLabel && /\bd-?flash\b/i.test(apiLabel)) return apiLabel;
    return friendlyStackLabel(model);
  }

  function isDflashStack(model) {
    return !!(model && model.dflash_stack && model.draft_path);
  }

  function defaultOptionLabel(model) {
    const isStack = isDflashStack(model);
    const name = isStack
      ? stackDisplayName(model)
      : String(model.label || model.filename || model.id || 'Model').replace(/\.gguf$/i, '');
    const parts = [name];
    const nameHasQuant = /\b(?:Q\d|IQ\d|F16|F32|BF16)\b/i.test(name);
    if (!isStack && model.quant && model.quant !== '—' && !nameHasQuant) parts.push(model.quant);
    if (model.size_gb != null) parts.push(`${model.size_gb} GB`);
    const text = parts.join(' · ');
    return isStack ? `${text} (DFS)` : text;
  }

  function normalizePickerPath(path) {
    return String(path || '').trim().replace(/\\/g, '/').toLowerCase();
  }

  function isPickerVisibleModel(model, allModels) {
    if (!model) return false;
    if (model.library_file) return false;
    if (isAcceleratorOnlyModel(model)) return false;
    if (isHashLabel(model.label) || isHashLabel(model.filename) || isHashLabel(model.id)) return false;
    const pool = allModels || [];
    const pathKey = normalizePickerPath(model.path);
    const fileKey = String(model.filename || '').trim().toLowerCase();
    if (fileKey && !isDflashStack(model)) {
      if (pool.some((entry) => (
        entry !== model
        && isDflashStack(entry)
        && String(entry.filename || '').trim().toLowerCase() === fileKey
      ))) {
        return false;
      }
    }
    if (pathKey && !model.dflash_stack && !model.draft_path) {
      if (pool.some((entry) => (
        entry !== model
        && entry.dflash_stack
        && entry.draft_path
        && normalizePickerPath(entry.path) === pathKey
      ))) {
        return false;
      }
    }
    if (model.dflash_stack && fileKey && !(model.server_id && model.loadable)) {
      if (pool.some((entry) => (
        entry !== model
        && entry.dflash_stack
        && entry.draft_path
        && entry.server_id
        && entry.loadable
        && (
          (pathKey && normalizePickerPath(entry.path) === pathKey)
          || String(entry.filename || '').trim().toLowerCase() === fileKey
        )
      ))) {
        return false;
      }
    }
    return true;
  }

  function pickerIdentityKey(model) {
    const file = String(model?.filename || '').trim().toLowerCase();
    if (isDflashStack(model) && file) return `stack:${file}`;
    if (file) {
      const size = Number(model.size_gb);
      const sizePart = Number.isFinite(size) && size > 0 ? `@${Math.round(size * 100)}` : '';
      return `file:${file}${sizePart}`;
    }
    return normalizePickerPath(model?.path) || String(model?.id || model?.server_id || '');
  }

  function isConsoleDiskPath(model) {
    const path = normalizePickerPath(model?.path);
    return path.includes('/dflash-console/') || path.includes('/dflash console/');
  }

  function pickerRank(model) {
    if (model?.library_file) return -100;
    let rank = 0;
    if (model?.server_id && model?.loadable) rank += 50;
    if (isConsoleRegisteredModel(model)) rank += 20;
    if (isConsoleDiskPath(model)) rank += 15;
    if (isDflashStack(model) && model?.loadable) rank += 10;
    if (model?.loadable) rank += 5;
    return rank;
  }

  function dedupePickerModels(models) {
    const best = new Map();
    for (const model of models || []) {
      const key = pickerIdentityKey(model);
      if (!key) continue;
      const prev = best.get(key);
      if (!prev || pickerRank(model) > pickerRank(prev)) best.set(key, model);
    }
    const chosen = new Set(best.values());
    return (models || []).filter((model) => chosen.has(model));
  }

  function modelsForLoadPicker(models, loadEngine, isHfEngineModel) {
    if (typeof isHfEngineModel !== 'function') return models || [];
    const hf = loadEngine === 'vllm' || loadEngine === 'transformers';
    return (models || []).filter((model) => {
      const hfModel = isHfEngineModel(model);
      return hf ? hfModel : !hfModel || model.server_id || model.loadable;
    });
  }

  function renderGroupedSelectOptions(models, {
    catalogKey,
    optionLabel,
    placeholder = 'Select…',
    selectedKey = '',
    consoleFirst = false,
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

    const pool = uniqueModels(models);
    const visiblePool = dedupePickerModels(pool.filter((model) => isPickerVisibleModel(model, pool)));

    if (consoleFirst) {
      const consoleModels = [];
      const externalBuckets = new Map();
      for (const model of visiblePool) {
        if (isConsoleRegisteredModel(model)) {
          consoleModels.push(model);
        } else {
          const label = externalGroupLabelFor(model);
          if (!externalBuckets.has(label)) externalBuckets.set(label, []);
          externalBuckets.get(label).push(model);
        }
      }
      const parts = [`<option value="">${escapeHtml(placeholder)}</option>`];
      if (consoleModels.length) {
        parts.push('<optgroup label="DFlash Console">');
        for (const model of sortModelsAlphabetically(consoleModels, { optionLabel: labelFn })) parts.push(optionHtml(model));
        parts.push('</optgroup>');
      }
      const externalLabels = [...externalBuckets.keys()].sort((a, b) => a.localeCompare(b));
      for (const label of externalLabels) {
        parts.push(`<optgroup label="${escapeHtml(label)}">`);
        for (const model of sortModelsAlphabetically(externalBuckets.get(label), { optionLabel: labelFn })) parts.push(optionHtml(model));
        parts.push('</optgroup>');
      }
      return parts.join('');
    }

    const { groups, buckets } = groupCatalogModels(visiblePool, { catalogKey: keyFn });
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
    isConsoleRegisteredModel,
    isConsoleDiskPath,
    sourceIdFor,
    sourceLabelFor,
    sourceOptions,
    sortModels,
    sortModelsAlphabetically,
    groupCatalogModels,
    defaultOptionLabel,
    friendlyStackLabel,
    stackDisplayName,
    isPickerVisibleModel,
    dedupePickerModels,
    isAcceleratorOnlyModel,
    acceleratorGenerationLabel,
    modelsForLoadPicker,
    renderGroupedSelectOptions,
  };
})();
