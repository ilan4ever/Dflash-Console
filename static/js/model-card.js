/** Shared model-card presentation: identity, modality, DFlash role, and paths. */
(function () {
  const MODALITY_BADGES = {
    llm: ['LLM', 'blue'],
    embedding: ['Embed', 'purple'],
    'speech-to-text': ['STT', 'green'],
    'text-to-speech': ['TTS', 'green'],
    vision: ['Vision', 'purple'],
    ocr: ['OCR', 'yellow'],
    translation: ['Translate', 'blue'],
    projector: ['Projector', 'violet'],
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function identityText(model) {
    return [
      model?.filename,
      model?.label,
      model?.id,
      model?.model_id,
      model?.repo_id,
      model?.title,
      model?.model_name,
    ].filter(Boolean).join(' ').toLowerCase();
  }

  function shortPath(path) {
    const text = String(path || '').replace(/\\/g, '/');
    const parts = text.split('/').filter(Boolean);
    if (parts.length <= 3) return text;
    return `…/${parts.slice(-3).join('/')}`;
  }

  function formatSizeGb(value) {
    const size = Number(value);
    if (!Number.isFinite(size) || size <= 0) return '';
    return `${size >= 10 ? Math.round(size) : size.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')} GB`;
  }

  function stackPart(model, role) {
    const parts = Array.isArray(model?.stack_details) ? model.stack_details : [];
    return parts.find((part) => String(part?.role || '').toLowerCase() === role)
      || parts.find((part) => role === 'draft' && String(part?.role || '').toLowerCase().startsWith('draft'))
      || null;
  }

  function targetPathFor(model) {
    const direct = String(model?.path || model?.model_path || '').trim();
    if (direct) return direct;
    return String(stackPart(model, 'target')?.path || '').trim();
  }

  function acceleratorPathFor(model) {
    const direct = String(
      model?.draft_path
      || model?.accelerator_path
      || model?.draftPath
      || '',
    ).trim();
    if (direct) return direct;
    const draftPath = String(stackPart(model, 'draft')?.path || '').trim();
    if (draftPath) return draftPath;
    const role = String(model?.role || '').toLowerCase();
    if (model?.accelerator_only === true || role === 'accelerator' || role.startsWith('draft-')) {
      return String(model?.path || model?.model_path || '').trim();
    }
    return '';
  }

  function acceleratorSizeFor(model) {
    const direct = model?.draft_size_gb ?? model?.accelerator_size_gb;
    if (direct != null) return direct;
    return stackPart(model, 'draft')?.size_gb;
  }

  function acceleratorNameFor(model) {
    const path = acceleratorPathFor(model);
    if (path) return path.replace(/\\/g, '/').split('/').pop() || '';
    return String(
      model?.draft_filename
      || model?.accelerator_filename
      || stackPart(model, 'draft')?.name
      || '',
    ).trim();
  }

  function isStack(model) {
    if (model?.accelerator_only === true) return false;
    const caps = Array.isArray(model?.capabilities) ? model.capabilities : [];
    return !!(
      model?.dflash_stack
      || model?.draft_path
      || caps.includes('dflash')
      || acceleratorPathFor(model)
    );
  }

  function isAccelerator(model) {
    if (!model) return false;
    if (model.accelerator_only === true) return true;
    if (model.accelerator_only === false) return false;
    if (isStack(model)) return false;
    const size = Number(model.size_gb);
    if (Number.isFinite(size) && size > 8) return false;
    const role = String(model.role || '').toLowerCase();
    if (role === 'draft-dflash' || role === 'draft-dspark' || role === 'accelerator') return true;
    if (role === 'loaded-model' || role === 'alias' || model.plain_llm || model.is_adhoc) return false;
    if (window.DFlashModelGroups?.isAcceleratorOnlyModel) {
      return window.DFlashModelGroups.isAcceleratorOnlyModel(model);
    }
    const leaf = (value) => {
      const raw = String(value || '').trim().replace(/[\\/]+$/, '');
      if (!raw) return '';
      const parts = raw.split(/[\\/]/);
      return parts[parts.length - 1] || '';
    };
    const name = [
      leaf(model?.filename) || model?.filename,
      leaf(model?.path),
      leaf(model?.model_path),
    ].filter(Boolean).join(' ').toLowerCase();
    if (!name.trim()) return false;
    return /(?:^|[^a-z])draft(?:[^a-z]|$)/.test(name) || /dflash|dspark/.test(name);
  }

  function generationLabel(model) {
    const explicit = String(
      model?.dflash_generation_label
      || (model?.dflash_generation ? `DFlash ${String(model.dflash_generation).replace(/^dflash/i, '')}` : '')
      || '',
    ).trim();
    if (explicit) return explicit;
    const name = [
      identityText(model),
      acceleratorNameFor(model),
      acceleratorPathFor(model),
    ].join(' ').toLowerCase();
    if (/dflash[-_.]?2|dflash2/.test(name)) return 'DFlash 2';
    if (/dflash|dspark/.test(name)) return 'DFlash 1';
    return '';
  }

  function modality(model) {
    if (isProjector(model)) return 'projector';
    const caps = Array.isArray(model?.capabilities) ? model.capabilities : [];
    const explicit = String(model?.modality || '').trim().toLowerCase();
    const kind = String(model?.model_kind || model?.kind || '').trim().toLowerCase();
    const text = [
      identityText(model),
      model?.pipeline_tag,
      model?.model_kind_label,
    ].join(' ').toLowerCase();
    if (kind === 'stt' || kind === 'faster-whisper' || /whisper|speech-to-text|\bstt\b/.test(text)) {
      return 'speech-to-text';
    }
    if (kind === 'tts' || kind === 'piper' || kind === 'vibevoice' || /piper|text-to-speech|\btts\b/.test(text)) {
      return 'text-to-speech';
    }
    if (kind === 'embedding' || /embed|nomic|feature-extraction/.test(text)) return 'embedding';
    if (explicit === 'ocr' || /ocr|chandra|ovis|paddleocr|olmocr|image-to-text/.test(text)) return 'ocr';
    if (explicit === 'translation' || /translat|nllb|opus-mt|madlad|seamless|tower/.test(text)) {
      return 'translation';
    }
    // Chat/instruct models stay LLM even when they also expose vision.
    if (caps.includes('llm') || caps.includes('instruct') || explicit === 'llm') return 'llm';
    if (MODALITY_BADGES[explicit]) return explicit;
    if (/vision|multimodal|[-_]vl[-_]/.test(text) || caps.includes('vision')) return 'vision';
    return 'llm';
  }

  function isProjector(model) {
    if (!model) return false;
    if (model.is_projector === true) return true;
    if (Array.isArray(model?.capabilities) && model.capabilities.includes('projector')) return true;
    const name = `${model?.filename || ''} ${model?.path || ''}`.toLowerCase();
    return name.endsWith('.gguf') && /mmproj/.test(name);
  }

  function isReasoning(model) {
    if (isProjector(model)) return false;
    if (isAccelerator(model)) return false;
    if (model?.reasoning === true) return true;
    return Array.isArray(model?.capabilities) && model.capabilities.includes('reasoning');
  }

  function tag(label, tone = 'blue', title = '') {
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
    return `<span class="lm-badge ${tone}"${titleAttr}>${escapeHtml(label)}</span>`;
  }

  function dflashLogo(label) {
    const safeLabel = escapeHtml(label);
    return `<span class="lm-tag gold dflash-logo-label" role="img" aria-label="${safeLabel}" title="${safeLabel}"></span>`;
  }

  function classificationTags(model, { includeReasoning = true } = {}) {
    const tags = [];
    const type = modality(model);
    const modalityEntry = MODALITY_BADGES[type];
    if (modalityEntry) {
      tags.push(tag(modalityEntry[0], modalityEntry[1], `Modality: ${type}`));
    }
    if (isAccelerator(model)) {
      const generation = generationLabel(model) || 'DFlash 1';
      tags.push(dflashLogo(`${generation} accelerator`));
      tags.push(tag('Accelerator', 'orange', `${generation} draft accelerator; not a target model`));
    } else if (isStack(model)) {
      const generation = generationLabel(model);
      tags.push(dflashLogo(generation ? `${generation} stack` : 'DFlash stack'));
    }
    if (includeReasoning && isReasoning(model)) {
      tags.push('<span class="lm-badge reasoning" title="This model exposes a thinking/reasoning mode">Reasoning</span>');
    }
    return tags.join('');
  }

  function detailLine(label, path, size) {
    const pathText = shortPath(path);
    const sizeText = formatSizeGb(size);
    const suffix = sizeText ? ` · ${sizeText}` : '';
    const text = `${label} · ${pathText || 'path unavailable'}${suffix}`;
    const title = path ? `${label}: ${path}${sizeText ? ` · ${sizeText}` : ''}` : text;
    return `<div class="lm-model-card-detail-line" title="${escapeHtml(title)}">${escapeHtml(text)}</div>`;
  }

  function detailsHtml(model, {
    includeTarget = true,
    includeAccelerator = true,
    alwaysForStack = true,
  } = {}) {
    const acceleratorPath = acceleratorPathFor(model);
    const targetPath = targetPathFor(model);
    const stack = isStack(model);
    const lines = [];
    if (includeTarget && targetPath && (!isAccelerator(model) && (alwaysForStack ? stack : true))) {
      const targetPart = stackPart(model, 'target');
      lines.push(detailLine('Target', targetPath, targetPart?.size_gb ?? model?.size_gb));
    }
    if (includeAccelerator && acceleratorPath) {
      lines.push(detailLine(
        'Accelerator',
        acceleratorPath,
        acceleratorSizeFor(model),
      ));
    }
    if (!lines.length) return '';
    return `<div class="lm-model-card-details">${lines.join('')}</div>`;
  }

  function presentation(model) {
    const accelerator = isAccelerator(model);
    const stack = isStack(model);
    return {
      modality: modality(model),
      accelerator,
      stack,
      reasoning: isReasoning(model),
      generation: generationLabel(model),
      targetPath: targetPathFor(model),
      acceleratorPath: acceleratorPathFor(model),
      acceleratorName: acceleratorNameFor(model),
      targetSizeGb: model?.size_gb,
      acceleratorSizeGb: acceleratorSizeFor(model),
    };
  }

  window.DFlashModelCard = {
    MODALITY_BADGES,
    identityText,
    shortPath,
    formatSizeGb,
    isAccelerator,
    isProjector,
    isStack,
    isReasoning,
    modality,
    generationLabel,
    presentation,
    classificationTags,
    detailsHtml,
  };
})();
