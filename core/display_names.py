"""Rich client-facing model names and catalog metadata for external apps."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_DFLASH_ENGINE_MARKER = 'dflash'

_FAMILY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'gemma[-\s]?4', re.I), 'Gemma 4'),
    (re.compile(r'gemma[-\s]?3', re.I), 'Gemma 3'),
    (re.compile(r'qwen3\.?5', re.I), 'Qwen 3.5'),
    (re.compile(r'qwen3\.?6', re.I), 'Qwen 3.6'),
    (re.compile(r'qwen3[-\s]?\.?8', re.I), 'Qwen 3.8'),
    (re.compile(r'qwen', re.I), 'Qwen'),
    (re.compile(r'bonsai', re.I), 'Bonsai'),
)

_LAB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'(?:^|[/\\])google[/\\]', re.I), 'Google'),
    (re.compile(r'gemma', re.I), 'Google'),
    (re.compile(r'qwen', re.I), 'Qwen'),
    (re.compile(r'meta-llama|llama', re.I), 'Meta'),
    (re.compile(r'mistral', re.I), 'Mistral'),
)

_QUANT_RE = re.compile(
    r'(?:^|[\-_])(q\d+(?:_[0-9]+)?(?:_[klmxs]+(?:_[klmxs]+)?)?|f16|bf16|iq\d+(?:_[klmxs]+)?)(?:[\-_.]|$)',
    re.I,
)

_SIZE_RE = re.compile(r'(\d+(?:\.\d+)?)\s*b\b', re.I)


def _stack_by_role(model_stack: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in model_stack:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get('role') or '').strip()
        if role:
            out[role] = entry
    return out


def _normalize_path_text(path: str) -> str:
    return str(path or '').replace('\\', '/')


def infer_lab(*, path: str = '', source: str = '', model_id: str = '', label: str = '') -> str:
    haystack = ' '.join([path, source, model_id, label])
    normalized = _normalize_path_text(haystack)
    for pattern, lab in _LAB_PATTERNS:
        if pattern.search(normalized):
            return lab
    source_key = str(source or '').strip().lower()
    if source_key == 'dflash':
        return 'DFlash'
    if source_key == 'lmstudio':
        return 'LM Studio library'
    return ''


def _infer_family(*texts: str) -> str:
    haystack = ' '.join(str(item or '') for item in texts if str(item or '').strip())
    for pattern, family in _FAMILY_PATTERNS:
        if pattern.search(haystack):
            return family
    return ''


def _infer_parameter_size(*texts: str) -> str:
    for text in texts:
        match = _SIZE_RE.search(str(text or ''))
        if match:
            raw = match.group(1)
            if '.' in raw:
                return f'{raw}B'
            return f'{int(float(raw))}B'
    return ''


def _infer_quantization(*texts: str) -> tuple[str, str]:
    for text in texts:
        match = _QUANT_RE.search(str(text or '').replace('.', '-'))
        if not match:
            continue
        full = match.group(1).upper().replace('_', '_')
        short = full.split('_')[0]
        if short.startswith('IQ'):
            short = full
        return short, full
    return '', ''


def _infer_variant(model_id: str, target_id: str, filename: str) -> str:
    for text in (model_id, target_id, filename):
        lowered = str(text or '').lower()
        if 'it-qat' in lowered or 'qat' in lowered:
            return 'it-qat'
        if '-it' in lowered or lowered.endswith('it'):
            return 'it'
        if 'instruct' in lowered:
            return 'instruct'
    return ''


def _family_drop_tokens(family: str) -> set[str]:
    tokens: set[str] = set()
    for part in re.split(r'[\s\-_./]+', str(family or '').lower()):
        if part:
            tokens.add(part)
    if 'gemma' in tokens:
        tokens.add('gemma4')
        tokens.add('gemma-4')
    if any(part.startswith('qwen') for part in tokens):
        tokens.update({
            'qwen',
            'qwen3',
            'qwen35',
            'qwen3.5',
            'qwen36',
            'qwen3.6',
            'qwen38',
            'qwen3.8',
        })
    return tokens


def _source_distinctive_suffix(
    api_model_id: str,
    target_model_id: str,
    *,
    family: str = '',
    parameter_size: str = '',
    quantization_full: str = '',
) -> str:
    """Trailing source tokens after family/size, e.g. gemma-4-12b-it-qat -> it qat."""
    drop = _family_drop_tokens(family)
    size_token = str(parameter_size or '').strip().lower().rstrip('b')
    quant_tokens = {
        token
        for token in re.split(r'[-_./\s]+', str(quantization_full or '').lower())
        if token
    }
    for raw in (api_model_id, target_model_id):
        text = str(raw or '').strip().lower()
        if not text:
            continue
        tokens = [token for token in re.split(r'[-_/]+', text.replace('.', '-')) if token]
        kept: list[str] = []
        for token in tokens:
            if token in drop:
                continue
            if size_token and token.rstrip('b') == size_token:
                continue
            if token in quant_tokens or token in {'dflash', 'dspark'}:
                continue
            if re.fullmatch(r'qwen3?\.?5', token) or token in {'qwen35', 'qwen36'}:
                continue
            if token.startswith('gemma'):
                continue
            kept.append(token)
        if kept:
            return ' '.join(kept)
    return ''


def _title_core(
    family: str,
    parameter_size: str,
    *,
    source_suffix: str = '',
    variant: str = '',
    quantization: str = '',
    include_quantization: bool = False,
) -> str:
    parts = [part for part in (family, parameter_size) if part]
    suffix = str(source_suffix or '').strip()
    if not suffix and variant:
        suffix = str(variant).replace('-', ' ').strip()
    if suffix:
        parts.append(suffix)
    if include_quantization and quantization:
        parts.append(quantization)
    if parts:
        return ' '.join(parts)
    return ''


_FRIENDLY_QUANT_RE = re.compile(
    r'(?i)(?:^|[\s_\-])(?:Q\d(?:[_A-Z0-9]+)?|IQ\d[_A-Z0-9]+|F16|F32|BF16)(?:$|[\s_\-])'
)
_FRIENDLY_NOISE_RE = re.compile(
    r'(?i)\b(?:gguf|instruct|chat|it|qat|draft|llama|cpp|ud|of)\b'
)
_FRIENDLY_ORG_RE = re.compile(
    r'(?i)^(qwen|google|bartowski|lmstudio|meta|mistral|microsoft)[\s_\-]+'
)


def friendly_stack_label(name: str | Path) -> str:
    """Short UI name: family, version, size, and a D-Flash mark. No quant."""
    stem = Path(str(name or '')).stem
    stem = _FRIENDLY_QUANT_RE.sub(' ', f' {stem} ').strip()
    stem = stem.replace('_', ' ').replace('-', ' ')
    stem = _FRIENDLY_ORG_RE.sub(lambda match: f'{match.group(1)} ', stem, count=1)
    stem = _FRIENDLY_NOISE_RE.sub(' ', stem)
    stem = re.sub(r'(?i)\b(qwen|gemma)\s+\1(?=\d)', r'\1', stem)
    stem = re.sub(r'(?i)\b(qwen)(\d)', r'Qwen \2', stem)
    stem = re.sub(r'(?i)\b(gemma)(\d)', r'Gemma \2', stem)
    stem = re.sub(r'\s+', ' ', stem).strip(' .')
    words: list[str] = []
    for word in stem.split():
        lower = word.lower()
        if lower in {'qwen', 'gemma', 'deepseek', 'bonsai', 'laguna'}:
            words.append(word[:1].upper() + word[1:].lower())
        elif re.fullmatch(r'\d+(?:\.\d+)?[Bb]', word):
            words.append(word.upper().replace('b', 'B'))
        elif re.fullmatch(r'[A-Za-z]\d+[A-Za-z]?', word):
            words.append(word.upper())
        else:
            words.append(word)
    label = ' '.join(words).strip()
    if not label:
        label = Path(str(name or '')).stem or 'Model'
    if not re.search(r'(?i)\bd-?flash\b', label):
        label = f'{label} D-Flash'
    return label


def _ensure_dflash_engine_marker(title_core: str) -> str:
    """Console titles must identify DFlash engines — not plain LM Studio models."""
    core = str(title_core or '').strip()
    if not core:
        return _DFLASH_ENGINE_MARKER
    if _DFLASH_ENGINE_MARKER in core.lower().replace('-', ' '):
        return core
    return f'{core} {_DFLASH_ENGINE_MARKER}'.strip()


def _strip_client_provider_suffix(text: str) -> str:
    cleaned = str(text or '').strip()
    cleaned = re.sub(r'\s*\((DFC|DFlash Console)\)\s*$', '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def build_model_catalog(server: dict[str, Any], model_stack: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build structured catalog metadata from engine config + resolved stack."""
    stack = [entry for entry in (model_stack or []) if isinstance(entry, dict)]
    by_role = _stack_by_role(stack)
    alias = by_role.get('alias') or {}
    target = by_role.get('target') or {}
    draft = by_role.get('draft-dflash') or by_role.get('draft-dspark') or {}

    api_model_id = str(server.get('model_id') or alias.get('id') or '').strip()
    target_path = str(target.get('path') or '')
    target_filename = Path(target_path).name if target_path else str(target.get('label') or '')
    target_model_id = str(target.get('id') or '').strip()
    draft_model_id = str(draft.get('id') or '').strip()
    draft_path = str(draft.get('path') or '')

    text_sources = [
        target_filename,
        target_path,
        target_model_id,
        api_model_id,
        str(server.get('label') or ''),
        str(server.get('profile') or ''),
    ]
    family = _infer_family(*text_sources)
    parameter_size = _infer_parameter_size(*text_sources)
    quantization, quantization_full = _infer_quantization(
        target_filename,
        target_path,
        target_model_id,
        api_model_id,
    )
    lab = infer_lab(
        path=target_path,
        source=str(target.get('source') or ''),
        model_id=api_model_id,
        label=str(target.get('label') or server.get('label') or ''),
    )
    variant = _infer_variant(api_model_id, target_model_id, target_filename)
    source_suffix = _source_distinctive_suffix(
        api_model_id,
        target_model_id,
        family=family,
        parameter_size=parameter_size,
        quantization_full=quantization_full,
    )
    title_source_suffix = source_suffix
    if draft and 'dflash' not in title_source_suffix.lower() and 'dspark' not in title_source_suffix.lower():
        title_source_suffix = f'{title_source_suffix} dflash'.strip()
    title_core_ui = _title_core(
        family,
        parameter_size,
        source_suffix=title_source_suffix,
        variant=variant,
    )
    if not title_core_ui:
        title_core_ui = str(server.get('label') or api_model_id or 'DFlash model').strip()
    title_core_ui = _ensure_dflash_engine_marker(title_core_ui)
    title_core_full = (
        f'{title_core_ui} {quantization}'.strip()
        if quantization
        else title_core_ui
    )

    return {
        'display_name': title_core_ui,
        'display_name_full': title_core_full,
        'family': family,
        'parameter_size': parameter_size,
        'quantization': quantization,
        'quantization_full': quantization_full,
        'source_suffix': source_suffix,
        'lab': lab,
        'variant': variant,
        'api_model_id': api_model_id,
        'target_model_id': target_model_id,
        'target_filename': target_filename,
        'target_path': target_path,
        'target_source': str(target.get('source') or '').strip(),
        'draft_model_id': draft_model_id,
        'draft_path': draft_path,
        'draft_source': str(draft.get('source') or '').strip(),
        'engine_id': str(server.get('id') or '').strip(),
        'profile': str(server.get('profile') or '').strip(),
        'console_label': str(server.get('label') or '').strip(),
    }


def build_engine_client_metadata(
    server: dict[str, Any],
    model_stack: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    catalog = build_model_catalog(server, model_stack)
    return {
        'display_name': catalog['display_name'],
        'display_name_full': catalog['display_name_full'],
        'model_catalog': catalog,
    }


def client_display_name(server: dict[str, Any], model_stack: list[dict[str, Any]] | None = None) -> str:
    return build_model_catalog(server, model_stack)['display_name']
