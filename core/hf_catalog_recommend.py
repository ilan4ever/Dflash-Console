"""Pick 1–3 catalog rows that are best for this PC."""

from __future__ import annotations

import math
import re
from typing import Any

_MAX_RECOMMENDED = 3

_OFFICIAL_AUTHORS = frozenset({
    'google',
    'google-bert',
    'google-t5',
    'meta-llama',
    'meta',
    'facebook',
    'openai',
    'mistralai',
    'qwen',
    'deepseek-ai',
    'microsoft',
    'ibm-granite',
    'nvidia',
    'stabilityai',
    'black-forest-labs',
    'cohere',
    'huggingface',
    'openai-community',
    'baai',
    'nomic-ai',
})

_QUALITY_GGUF_AUTHORS = frozenset({
    'bartowski',
    'unsloth',
    'lmstudio-community',
    'ggml-org',
    'thebloke',
    'google',
})

_QUANT_TAIL_RE = re.compile(
    r'(?:-gguf)+$'
    r'|-(?:qat)(?:-.*)?$'
    r'|-(?:i?q)\d[\w-]*$'
    r'|-(?:q[2-8](?:[_-]k(?:[_-][sml])?|[_-]0)?)$',
    re.I,
)
_SLUG_CLEAN_RE = re.compile(r'[^a-z0-9]+')


def _repo_slug(row: dict[str, Any]) -> str:
    repo_id = str(row.get('id') or '').strip()
    return repo_id.split('/')[-1].lower() if '/' in repo_id else repo_id.lower()


def _author(row: dict[str, Any]) -> str:
    repo_id = str(row.get('id') or '').strip()
    author = str(row.get('author') or '').strip()
    if author:
        return author.lower()
    return repo_id.split('/')[0].lower() if '/' in repo_id else ''


def _has_gguf(row: dict[str, Any]) -> bool:
    tags = [str(tag).lower() for tag in (row.get('tags') or []) if tag]
    return bool(row.get('has_gguf')) or int(row.get('gguf_count') or 0) > 0 or any(
        'gguf' in tag for tag in tags
    )


def _format_kind(row: dict[str, Any]) -> str:
    if row.get('accelerator_only'):
        return 'accel'
    if _has_gguf(row):
        return 'gguf'
    return 'full'


def family_key(row: dict[str, Any]) -> str:
    slug = _SLUG_CLEAN_RE.sub('-', _repo_slug(row)).strip('-')
    slug = _QUANT_TAIL_RE.sub('', slug)
    slug = re.sub(r'^(?:google|meta|qwen)-', '', slug)
    return slug or _repo_slug(row)


def _downloads(row: dict[str, Any]) -> int:
    try:
        return max(0, int(row.get('downloads') or 0))
    except (TypeError, ValueError):
        return 0


def catalog_recommendation_score(row: dict[str, Any]) -> int:
    """Higher is a better pick for this machine."""
    if not isinstance(row, dict):
        return -10_000
    slug = _repo_slug(row)
    author = _author(row)
    kind = _format_kind(row)
    score = 0
    if row.get('accelerator_only'):
        score -= 600
    if row.get('fits_machine') is True:
        score += 1000
    elif row.get('fits_machine_uncertain') is True:
        score += 180
    else:
        score -= 350
    if row.get('local_ready') or row.get('catalog_ready_to_load'):
        score += 280
    if row.get('runnable') is True:
        score += 140
    if kind == 'gguf':
        score += 220
        if author in _QUALITY_GGUF_AUTHORS:
            score += 50
    if author in _OFFICIAL_AUTHORS:
        score += 200
        if kind == 'full':
            score += 90
    if re.search(r'(?:^|-)(?:it|instruct)(?:-|$)', slug):
        score += 45
    if re.search(r'uncensored|abliterat|nsfw', slug):
        score -= 90
    score += min(int(math.log10(_downloads(row) + 1) * 42), 260)
    return score


def _is_quality_pick(row: dict[str, Any]) -> bool:
    if _author(row) in _OFFICIAL_AUTHORS or _author(row) in _QUALITY_GGUF_AUTHORS:
        return True
    if row.get('local_ready') or row.get('catalog_ready_to_load'):
        return True
    return _downloads(row) >= 100_000


def _order_picked(picked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep official/full ahead of the GGUF copy of the same model."""
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in picked:
        fam = family_key(row)
        if fam not in groups:
            order.append(fam)
            groups[fam] = []
        groups[fam].append(row)
    ordered: list[dict[str, Any]] = []
    for fam in order:
        group = groups[fam]
        group.sort(
            key=lambda row: (
                0 if _format_kind(row) == 'full' else 1,
                -catalog_recommendation_score(row),
            ),
        )
        ordered.extend(group)
    return ordered


def _can_pair(existing: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    fam = family_key(candidate)
    kind = _format_kind(candidate)
    for row in existing:
        if family_key(row) != fam:
            continue
        if _format_kind(row) == kind:
            return False
        # Same model family is OK once as official/full and once as GGUF.
        if {_format_kind(row), kind} != {'full', 'gguf'}:
            return False
    return True


def apply_catalog_recommendations(
    models: list[dict[str, Any]] | None,
    *,
    limit: int = _MAX_RECOMMENDED,
) -> list[dict[str, Any]]:
    """Mark and pin up to ``limit`` best-for-this-PC rows at the top."""
    rows = [row for row in (models or []) if isinstance(row, dict)]
    for row in rows:
        row.pop('catalog_recommended', None)
        row.pop('catalog_recommended_rank', None)
        row.pop('catalog_recommended_reason', None)
    if not rows:
        return rows

    ranked = sorted(
        rows,
        key=lambda row: (catalog_recommendation_score(row), _downloads(row)),
        reverse=True,
    )
    picked: list[dict[str, Any]] = []
    skip_accel = not all(row.get('accelerator_only') for row in rows)
    require_fit = any(row.get('fits_machine') is True for row in rows)
    for row in ranked:
        if len(picked) >= max(1, min(int(limit), _MAX_RECOMMENDED)):
            break
        if skip_accel and row.get('accelerator_only'):
            continue
        if require_fit and row.get('fits_machine') is not True:
            continue
        if picked and not _is_quality_pick(row):
            continue
        if not _can_pair(picked, row):
            continue
        picked.append(row)

    picked = _order_picked(picked)

    reasons = {
        1: 'Best match for this PC — fits your GPU, popular, and a trusted source when possible',
        2: 'Strong alternate for this PC',
        3: 'Another good option for this PC',
    }
    picked_ids = {str(row.get('id') or '') for row in picked}
    for index, row in enumerate(picked, start=1):
        row['catalog_recommended'] = True
        row['catalog_recommended_rank'] = index
        row['catalog_recommended_reason'] = reasons.get(index, reasons[2])
    rest = [row for row in rows if str(row.get('id') or '') not in picked_ids]
    return picked + rest
