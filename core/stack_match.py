"""Match target GGUF checkpoints to DFlash accelerators (local + Hugging Face)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.config import load_config, list_servers, suggest_server_port
from core.dflash_generation import (
    dflash_generation_label,
    hf_category_for_generation,
    infer_dflash_generation,
    normalize_dflash_generation,
    repo_dflash_generation,
    spec_draft_n_max,
)
from core.local_models import list_local_models
from core.model_presets import infer_profile_from_path, model_id_from_path

_PARAM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*[Bb]', re.I)
_QUANT_RE = re.compile(r'Q\d[_A-Z0-9]+|F16|BF16|IQ\d_[A-Z0-9]+', re.I)
_DFLASH_RE = re.compile(r'dflash|dspark', re.I)
_SHARD_RE = re.compile(r'[-_]\d{5}-of-\d{5}', re.I)
_TOKEN_RE = re.compile(r'[a-z0-9]+')
_MIN_CAPABLE_SCORE = 2.0
_TRUSTED_DFLASH2_AUTHORS = {'incoai'}
_TRUSTED_DFLASH1_AUTHORS = {'mrchuy'}
_POPULAR_MIRROR_AUTHORS = {'z-lab'}
_MIN_DFLASH2_RECOMMEND_SCORE = 8.0
_DFLASH2_SCORE_MARGIN = 0.75
_MIN_VIABLE_GENERATION_SCORE = 5.5


def is_accelerator_path(path: str | Path) -> bool:
    text = Path(path).name.lower()
    return bool(_DFLASH_RE.search(text))


def stack_target_block_reason(path: str | Path) -> dict[str, str] | None:
    """Why a GGUF cannot become a DFlash stack target — distinct from accelerators."""
    text = Path(path).name.lower()
    if not text.endswith('.gguf'):
        return None
    if text.startswith('mmproj') or '.mmproj' in text:
        return {
            'reason_code': 'projector',
            'reason': 'Vision projectors are companion files, not DFlash stack targets.',
        }
    if 'translategemma' in text:
        return {
            'reason_code': 'not-stack-target',
            'reason': 'TranslateGemma is a translation model, not a DFlash stack target.',
        }
    if text.startswith('mtp-'):
        return {
            'reason_code': 'not-stack-target',
            'reason': 'MTP companion files are not DFlash stack targets.',
        }
    if is_accelerator_path(path):
        return {
            'reason_code': 'accelerator',
            'reason': 'This is a DFlash or DSpark draft accelerator. Choose the full target GGUF instead.',
        }
    return None


def is_target_candidate(path: str | Path) -> bool:
    text = Path(path).name.lower()
    if not text.endswith('.gguf'):
        return False
    return stack_target_block_reason(path) is None


def is_viable_stack_pair(
    target_path: str | Path,
    accelerator_path: str | Path,
    score: float,
    *,
    min_score: float = _MIN_CAPABLE_SCORE,
) -> bool:
    target_name = Path(target_path).name.lower()
    accel_name = Path(accelerator_path).name.lower()
    if not is_target_candidate(target_path):
        return False
    if score < min_score:
        return False
    family_match = (
        ('qwen' in target_name and 'qwen' in accel_name)
        or ('gemma' in target_name and 'gemma' in accel_name)
        or ('bonsai' in target_name and ('bonsai' in accel_name or 'dspark' in accel_name))
        or ('deepseek' in target_name and ('qwen' in accel_name or 'deepseek' in accel_name))
    )
    if not family_match:
        return score >= 7.5
    target_param = _param_token(target_name)
    accel_param = _param_token(accel_name)
    if target_param and accel_param and target_param != accel_param:
        return score >= 7.0
    return True


def _identity_tokens(name: str) -> set[str]:
    lower = Path(name).stem.lower()
    lower = _QUANT_RE.sub(' ', lower)
    lower = _DFLASH_RE.sub(' ', lower)
    tokens = {tok for tok in _TOKEN_RE.findall(lower) if len(tok) > 1 and tok not in {'gguf', 'it', 'instruct', 'chat'}}
    return tokens


def _param_token(name: str) -> str | None:
    match = _PARAM_RE.search(name)
    return f"{match.group(1)}b".lower() if match else None


def _family_version_bonus(target_name: str, accel_name: str) -> float:
    """Prefer accelerators from the same model generation (e.g. Qwen3.8 vs Qwen3.5)."""
    target_lower = target_name.lower()
    accel_lower = accel_name.lower()
    bonus = 0.0
    if 'qwen' in target_lower and 'qwen' in accel_lower:
        target_ver = re.search(r'qwen3(?:\.(\d+))?', target_lower)
        accel_ver = re.search(r'qwen3(?:\.(\d+))?', accel_lower)
        if target_ver and accel_ver:
            if target_ver.group(1) == accel_ver.group(1):
                bonus += 1.25
            elif target_ver.group(1) and accel_ver.group(1):
                bonus -= 0.75
    if 'gemma' in target_lower and 'gemma' in accel_lower:
        target_gen = re.search(r'gemma[-_.]?(\d+)', target_lower)
        accel_gen = re.search(r'gemma[-_.]?(\d+)', accel_lower)
        if target_gen and accel_gen:
            if target_gen.group(1) == accel_gen.group(1):
                bonus += 1.0
            elif target_gen.group(1) != accel_gen.group(1):
                bonus -= 0.5
    return bonus


def score_accelerator_pair(target_path: str | Path, accelerator_path: str | Path) -> float:
    target_name = Path(target_path).name
    accel_name = Path(accelerator_path).name
    if not is_accelerator_path(accelerator_path):
        return 0.0
    target_tokens = _identity_tokens(target_name)
    accel_tokens = _identity_tokens(accel_name)
    if not target_tokens or not accel_tokens:
        return 0.0
    overlap = target_tokens & accel_tokens
    score = float(len(overlap))
    target_param = _param_token(target_name)
    accel_param = _param_token(accel_name)
    if target_param and accel_param:
        score += 4.0 if target_param == accel_param else -2.0
    if 'qwen' in target_name.lower() and 'qwen' in accel_name.lower():
        score += 1.5
    if 'gemma' in target_name.lower() and 'gemma' in accel_name.lower():
        score += 1.5
    score += _family_version_bonus(target_name, accel_name)
    if is_dflash2_accelerator_path(accel_name):
        score += 0.75
    return score


def infer_dflash_profile(target_path: str | Path, draft_path: str | Path | None = None) -> str:
    name = Path(target_path).name.lower()
    if 'bonsai' in name:
        return 'bonsai-spec'
    if 'gemma' in name:
        if re.search(r'12\s*b', name) or '12b' in name.replace('-', ''):
            return 'gemma-12-dflash'
        return 'gemma-chat'
    if 'qwen' in name or 'deepseek' in name:
        return 'qwen-dflash'
    if draft_path and is_accelerator_path(draft_path):
        draft_name = Path(draft_path).name.lower()
        if 'dspark' in draft_name:
            return 'bonsai-spec'
    return 'qwen-dflash'


def is_dflash2_accelerator_path(path: str | Path) -> bool:
    return infer_dflash_generation(path) == 'dflash2'


def build_hf_search_query(target_path: str | Path, *, dflash_generation: str = 'dflash1') -> str:
    stem = Path(target_path).stem
    stem = _SHARD_RE.sub('', stem)
    stem = _QUANT_RE.sub('', stem).strip(' .-_')
    stem = re.sub(r'\bud\b', ' ', stem, flags=re.I)
    stem = re.sub(r'\s+', ' ', stem.replace('_', ' ').replace('-', ' ')).strip()
    gen = normalize_dflash_generation(dflash_generation)
    accel_label = 'DFlash 2' if gen == 'dflash2' else 'DFlash'
    if not stem:
        return f'{accel_label} gguf'
    if 'dflash' not in stem.lower():
        return f'{stem} {accel_label} gguf'
    if gen == 'dflash2' and 'dflash2' not in stem.lower():
        return f'{stem} DFlash2 gguf'
    return f'{stem} gguf'


def _hf_accelerator_matches_target(target_path: str | Path, row: dict[str, Any]) -> bool:
    target_name = Path(target_path).name
    candidate_name = str(row.get('title') or row.get('id') or '')
    target_tokens = _identity_tokens(target_name)
    candidate_tokens = _identity_tokens(candidate_name)
    if not target_tokens or not candidate_tokens:
        return False
    overlap = target_tokens & candidate_tokens
    if not overlap:
        return False
    target_param = _param_token(target_name)
    candidate_param = _param_token(candidate_name)
    return not (target_param and candidate_param and target_param != candidate_param)


def suggest_stack_label(target_path: str | Path) -> str:
    from core.display_names import friendly_stack_label

    return friendly_stack_label(target_path)


def suggest_server_id(target_path: str | Path, *, cfg: dict[str, Any] | None = None) -> str:
    config = cfg or load_config()
    base = model_id_from_path(target_path)
    if not base.endswith('-dflash'):
        base = f'{base}-dflash'
    base = re.sub(r'[^a-z0-9-]', '-', base.lower())
    base = re.sub(r'-+', '-', base).strip('-')[:48] or 'custom-dflash'
    used = {str(row.get('id') or '') for row in list_servers(config)}
    if base not in used:
        return base
    idx = 2
    while f'{base}-{idx}' in used:
        idx += 1
    return f'{base}-{idx}'



def is_dflash_capable_target(target_path: str | Path, *, cfg: dict[str, Any] | None = None) -> bool:
    local = find_local_accelerators(target_path, cfg=cfg, limit=1)
    if not local:
        return False
    return float(local[0].get('score') or 0) >= _MIN_CAPABLE_SCORE


def preflight_stack_target(target_path: str | Path, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a user-facing answer before opening or submitting the stack wizard."""
    target = Path(str(target_path)).expanduser().resolve()
    identifier = model_id_from_path(target)
    base = {
        'success': True,
        'eligible': False,
        'target_path': str(target),
        'target_filename': target.name,
        'identifier': identifier,
        'hf_query': build_hf_search_query(target),
    }
    if not target.is_file():
        return {
            **base,
            'reason_code': 'missing-file',
            'reason': 'This model file is no longer available on disk.',
        }
    if target.suffix.lower() != '.gguf':
        return {
            **base,
            'reason_code': 'not-gguf',
            'reason': 'DFlash stacks can only use a GGUF target model.',
        }
    blocked = stack_target_block_reason(target)
    if blocked:
        return {
            **base,
            **blocked,
        }
    if not is_target_candidate(target):
        return {
            **base,
            'reason_code': 'not-stack-target',
            'reason': 'This model cannot be used as a DFlash stack target.',
        }
    if not identifier:
        return {
            **base,
            'reason_code': 'no-identifier',
            'reason': 'This file does not produce a usable model identifier. Rename it or choose another GGUF.',
        }

    local = find_local_accelerators(target, cfg=cfg, limit=5)
    if not local:
        return {
            **base,
            'reason_code': 'no-accelerator',
            'reason': 'No compatible DFlash accelerator is installed. Download one from Model catalog first.',
            'suggested_profile': infer_dflash_profile(target),
        }
    best = local[0]
    score = float(best.get('score') or 0)
    if not is_viable_stack_pair(target, best.get('path') or '', score):
        return {
            **base,
            'reason_code': 'weak-match',
            'reason': 'The available accelerator does not match this target strongly enough to create a safe stack.',
            'local_accelerators': local,
            'best_score': score,
        }
    return {
        **base,
        'eligible': True,
        'reason_code': 'ready',
        'reason': 'A compatible DFlash accelerator is available.',
        'local_accelerators': local,
        'best_accelerator': best,
        'suggested_profile': infer_dflash_profile(target, best.get('path') or ''),
        'suggested_label': suggest_stack_label(target),
        'suggested_model_id': identifier,
        'suggested_server_id': suggest_server_id(target, cfg=cfg),
        'suggested_port': suggest_server_port(cfg=cfg),
    }


def list_capable_targets(
    *,
    cfg: dict[str, Any] | None = None,
    models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    if models is None:
        catalog = list_local_models(cfg=config, scan_disk=True, force_refresh=False, include_dflash_stacks=False)
        models = list(catalog.get('models') or [])
        # A partial/profile-only catalog hides disk-scanned targets. Reuse the
        # last full scan (minus already-built stack cards) so pairing still works.
        if catalog.get('partial') or catalog.get('stale'):
            from core.local_models import _CATALOG_CACHE

            cached = list((_CATALOG_CACHE or {}).get('models') or [])
            if cached:
                models = [
                    row for row in cached
                    if not str(row.get('id') or '').startswith('stack-capable:')
                ]
    else:
        models = list(models)

    def _resolved_key(path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path).lower()

    accelerators: list[tuple[Path, str, dict[str, Any]]] = []
    for model in models:
        path_text = str(model.get('path') or '').strip()
        if not path_text:
            continue
        path = Path(path_text).expanduser().resolve()
        if path.is_file() and is_accelerator_path(path):
            accelerators.append((path, _resolved_key(path), model))

    rows: list[dict[str, Any]] = []
    for model in models:
        # Skip targets that already have a DFlash draft attached (registered stacks).
        if model.get('draft_path'):
            continue
        path_text = str(model.get('path') or '').strip()
        if not path_text:
            continue
        path = Path(path_text).expanduser().resolve()
        if not path.is_file() or not is_target_candidate(path):
            continue
        target_key = _resolved_key(path)
        scored: list[dict[str, Any]] = []
        for accel_path, accel_key, accel_model in accelerators:
            if accel_key == target_key:
                continue
            score = score_accelerator_pair(path, accel_path)
            if score <= 0:
                continue
            scored.append({
                'path': str(accel_path),
                'filename': accel_path.name,
                'label': accel_model.get('label') or accel_path.name,
                'size_gb': accel_model.get('size_gb'),
                'score': round(score, 2),
            })
        if not scored:
            # Keep the public matching seam usable for external or mocked
            # accelerator sources that are not present in the plain catalog.
            scored = find_local_accelerators(path, cfg=config, limit=5)
        scored.sort(key=lambda row: (-float(row.get('score') or 0), row.get('filename') or ''))
        scored = scored[:5]
        if not scored:
            continue
        best = scored[0]
        best_score = float(best.get('score') or 0)
        if not is_viable_stack_pair(path, best.get('path') or '', best_score):
            continue
        rows.append({
            'path': str(path),
            'filename': path.name,
            'label': model.get('label') or path.name,
            'source': str(model.get('source') or '').strip() or 'library',
            'size_gb': model.get('size_gb'),
            'publisher': model.get('publisher'),
            'arch': model.get('arch'),
            'params': model.get('params'),
            'quant': model.get('quant'),
            'modified': model.get('modified'),
            'accelerator_count': len(scored),
            'best_accelerator': best.get('filename'),
            'draft_path': best.get('path'),
            'draft_filename': best.get('filename'),
            'draft_size_gb': best.get('size_gb'),
            'match_score': best.get('score'),
        })
    rows.sort(key=lambda row: (-float(row.get('match_score') or 0), row.get('label') or ''))
    return {
        'success': True,
        'targets': rows,
        'total_count': len(rows),
    }


def find_local_accelerators(
    target_path: str | Path,
    *,
    cfg: dict[str, Any] | None = None,
    limit: int = 12,
    dflash_generation: str = 'auto',
) -> list[dict[str, Any]]:
    config = cfg or load_config()
    target = Path(str(target_path)).expanduser()
    if not target.is_file():
        return []
    gen_filter = normalize_dflash_generation(dflash_generation, default='auto')
    catalog = list_local_models(cfg=config, scan_disk=True, force_refresh=False, include_dflash_stacks=False)
    rows: list[dict[str, Any]] = []
    for model in catalog.get('models') or []:
        path_text = str(model.get('path') or '').strip()
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_file() or not is_accelerator_path(path):
            continue
        if path.resolve() == target.resolve():
            continue
        accel_gen = infer_dflash_generation(path)
        if gen_filter == 'dflash1' and accel_gen == 'dflash2':
            continue
        if gen_filter == 'dflash2' and accel_gen != 'dflash2':
            continue
        score = score_accelerator_pair(target, path)
        if score <= 0:
            continue
        rows.append({
            'path': str(path.resolve()),
            'filename': path.name,
            'label': model.get('label') or path.name,
            'size_gb': model.get('size_gb'),
            'publisher': model.get('publisher'),
            'score': round(score, 2),
            'source': model.get('source') or 'library',
            'dflash_generation': accel_gen,
            'dflash_generation_label': dflash_generation_label(accel_gen),
        })
    rows.sort(key=lambda row: (-float(row.get('score') or 0), row.get('filename') or ''))
    return rows[:limit]


def _resolved_path_key(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve()).lower()
    except OSError:
        return str(path).lower()


def _annotate_accelerator_rows(
    target: Path,
    rows: list[dict[str, Any]],
    *,
    current_draft_path: str | Path | None = None,
) -> tuple[float, dict[str, Any] | None]:
    """Mark each accelerator row with is_current / better_than_current."""
    current_key = _resolved_path_key(current_draft_path) if current_draft_path else ''
    current_score = 0.0
    current_row: dict[str, Any] | None = None
    if current_key:
        current_score = score_accelerator_pair(target, current_draft_path or '')
        current_row = {
            'path': str(Path(str(current_draft_path)).expanduser().resolve()),
            'filename': Path(str(current_draft_path)).name,
            'score': round(current_score, 2),
            'is_current': True,
            'better_than_current': False,
        }
    for row in rows:
        path_text = str(row.get('path') or '').strip()
        row_key = _resolved_path_key(path_text) if path_text else ''
        score = float(row.get('score') or score_accelerator_pair(target, path_text))
        row['score'] = round(score, 2)
        row['is_current'] = bool(current_key and row_key == current_key)
        row['better_than_current'] = bool(
            current_key
            and not row['is_current']
            and score > current_score + 0.01,
        )
    return current_score, current_row


def _hf_row_label(row: dict[str, Any]) -> str:
    return str(row.get('title') or row.get('label') or row.get('id') or '')


def _score_hf_accelerator(
    target: Path,
    row: dict[str, Any],
    *,
    current_score: float = 0.0,
) -> tuple[float, list[str]]:
    """Rank a Hugging Face accelerator repo for a target checkpoint."""
    label = _hf_row_label(row)
    score = score_accelerator_pair(target, label)
    reasons: list[str] = []
    author = str(row.get('author') or '').strip().lower()
    repo_id = str(row.get('id') or '').strip().lower()
    generation = str(row.get('dflash_generation') or repo_dflash_generation(repo_id, label))

    if author in _TRUSTED_DFLASH2_AUTHORS or repo_id.startswith('incoai/'):
        score += 2.0
        reasons.append('official DFlash 2 publisher')
    elif author in _TRUSTED_DFLASH1_AUTHORS:
        score += 1.75
        reasons.append('official Qwen3.8 DFlash 1 drafter')
    elif author in _POPULAR_MIRROR_AUTHORS:
        score += 1.25
        reasons.append('popular community mirror')

    downloads = int(row.get('downloads') or 0)
    if downloads >= 500:
        score += 0.5
        downloads_label = str(row.get('downloads_label') or '').strip()
        if downloads_label:
            reasons.append(f'{downloads_label} downloads')
        else:
            reasons.append(f'{downloads:,} downloads')

    if row.get('local_loadable'):
        score += 1.5
        reasons.append('ready to connect on this PC')
    elif row.get('local_ready'):
        score += 0.75
        reasons.append('partially installed on this PC')

    updated_days = row.get('updated_days')
    if isinstance(updated_days, int) and updated_days <= 7:
        score += 0.35
        updated_ago = str(row.get('updated_ago') or '').strip()
        if updated_ago:
            reasons.append(f'updated {updated_ago}')

    if generation == 'dflash2':
        reasons.insert(0, 'DFlash 2 drafter')
    elif generation == 'dflash1':
        reasons.insert(0, 'DFlash 1 drafter')

    if score > current_score + 0.25:
        reasons.insert(0, f'match {score:.1f} vs current {current_score:.1f}')

    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        key = reason.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(reason)
    return score, deduped[:4]


def _annotate_hf_accelerator_rows(
    target: Path,
    rows: list[dict[str, Any]],
    *,
    current_score: float = 0.0,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for row in rows:
        match_score, recommendation_reasons = _score_hf_accelerator(
            target,
            row,
            current_score=current_score,
        )
        enriched = {
            **row,
            'match_score': round(match_score, 2),
            'recommendation_reasons': recommendation_reasons,
            'is_recommended': False,
        }
        annotated.append(enriched)
    annotated.sort(
        key=lambda item: (
            -float(item.get('match_score') or 0),
            -int(item.get('downloads') or 0),
            str(item.get('id') or ''),
        ),
    )
    if annotated:
        annotated[0]['is_recommended'] = True
    return annotated


def _summarize_hf_suggestion(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': row.get('id'),
        'title': row.get('title') or row.get('id'),
        'author': row.get('author'),
        'lab': row.get('lab'),
        'size_label': row.get('size_label'),
        'size_gb': row.get('size_gb'),
        'url': row.get('url'),
        'downloads': row.get('downloads'),
        'downloads_label': row.get('downloads_label'),
        'likes': row.get('likes'),
        'updated_ago': row.get('updated_ago'),
        'updated_days': row.get('updated_days'),
        'description': row.get('description'),
        'dflash_generation': row.get('dflash_generation'),
        'dflash_generation_label': row.get('dflash_generation_label'),
        'local_ready': row.get('local_ready'),
        'local_loadable': row.get('local_loadable'),
        'catalog_ready_to_load': row.get('catalog_ready_to_load'),
        'match_score': row.get('match_score'),
        'recommendation_reasons': row.get('recommendation_reasons') or [],
        'is_recommended': bool(row.get('is_recommended')),
    }


def _fetch_hf_suggestion_rows(
    target: Path,
    generation: str,
    *,
    cfg: dict[str, Any] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    gen = normalize_dflash_generation(generation)
    hf_query = build_hf_search_query(target, dflash_generation=gen)
    rows: list[dict[str, Any]] = []
    try:
        from core.huggingface import search_models

        search = search_models(
            query=hf_query,
            limit=limit,
            category=hf_category_for_generation(gen),
        )
        for row in search.get('models') or []:
            if not _hf_accelerator_matches_target(target, row):
                continue
            row_gen = str(row.get('dflash_generation') or repo_dflash_generation(
                str(row.get('id') or ''),
                str(row.get('title') or ''),
            ))
            if gen == 'dflash1' and row_gen == 'dflash2':
                continue
            if gen == 'dflash2' and row_gen != 'dflash2':
                continue
            rows.append({
                **row,
                'dflash_generation': row_gen,
                'dflash_generation_label': dflash_generation_label(row_gen),
            })
    except Exception:
        return []
    return rows


def _generation_availability(
    target: Path,
    generation: str,
    *,
    cfg: dict[str, Any] | None = None,
    current_score: float = 0.0,
) -> dict[str, Any]:
    local = find_local_accelerators(target, cfg=cfg, dflash_generation=generation, limit=5)
    hf_rows = _fetch_hf_suggestion_rows(target, generation, cfg=cfg, limit=8)
    hf_ranked = _annotate_hf_accelerator_rows(target, hf_rows, current_score=current_score)
    best_local_score = float(local[0].get('score') or 0) if local else 0.0
    best_hf_score = float(hf_ranked[0].get('match_score') or 0) if hf_ranked else 0.0
    return {
        'generation': generation,
        'best_score': max(best_local_score, best_hf_score),
        'best_local_score': round(best_local_score, 2),
        'best_hf_score': round(best_hf_score, 2),
        'local_count': len(local),
        'hf_count': len(hf_ranked),
        'has_local': bool(local),
        'has_hf': bool(hf_ranked),
        'best_local': local[0] if local else None,
        'best_hf': hf_ranked[0] if hf_ranked else None,
    }


def resolve_recommended_generation(
    target: Path,
    *,
    cfg: dict[str, Any] | None = None,
    current_draft_path: str | Path | None = None,
) -> dict[str, Any]:
    """Pick DFlash 1 vs DFlash 2 for a target when the user has not chosen yet."""
    config = cfg or load_config()
    current_score = (
        score_accelerator_pair(target, current_draft_path)
        if current_draft_path
        else 0.0
    )
    gen1 = _generation_availability(
        target,
        'dflash1',
        cfg=config,
        current_score=current_score,
    )
    gen2 = _generation_availability(
        target,
        'dflash2',
        cfg=config,
        current_score=current_score,
    )
    d1 = float(gen1['best_score'] or 0)
    d2 = float(gen2['best_score'] or 0)
    reasons: list[str] = []
    recommend = 'dflash1'

    if d2 >= _MIN_DFLASH2_RECOMMEND_SCORE and d2 >= d1 + _DFLASH2_SCORE_MARGIN:
        recommend = 'dflash2'
        reasons.append('Strong DFlash 2 match for this target')
        if gen2.get('has_hf'):
            reasons.append('DFlash 2 accelerators are available on Hugging Face')
        if gen2.get('has_local'):
            reasons.append('A compatible DFlash 2 draft is already on this PC')
    elif d1 < _MIN_VIABLE_GENERATION_SCORE and d2 >= _MIN_VIABLE_GENERATION_SCORE:
        recommend = 'dflash2'
        reasons.append('No strong DFlash 1 match found')
        reasons.append('DFlash 2 is the best available option for this target')
    elif d2 < _MIN_VIABLE_GENERATION_SCORE:
        recommend = 'dflash1'
        reasons.append('DFlash 2 accelerators are not available for this target yet')
        if gen1.get('has_local') or gen1.get('has_hf'):
            reasons.append('DFlash 1 has working accelerator choices')
    elif d1 >= d2 + 0.5:
        recommend = 'dflash1'
        reasons.append('DFlash 1 has the stronger accelerator match for this target')
    else:
        recommend = 'dflash1'
        reasons.append('DFlash 1 is the safer default until DFlash 2 coverage improves')

    return {
        'recommended_generation': recommend,
        'recommended_generation_label': dflash_generation_label(recommend),
        'generation_recommendation_reasons': reasons[:3],
        'generation_scores': {
            'dflash1': round(d1, 2),
            'dflash2': round(d2, 2),
        },
        'generation_availability': {
            'dflash1': {
                'best_score': gen1['best_score'],
                'local_count': gen1['local_count'],
                'hf_count': gen1['hf_count'],
                'has_local': gen1['has_local'],
                'has_hf': gen1['has_hf'],
            },
            'dflash2': {
                'best_score': gen2['best_score'],
                'local_count': gen2['local_count'],
                'hf_count': gen2['hf_count'],
                'has_local': gen2['has_local'],
                'has_hf': gen2['has_hf'],
            },
        },
    }


def _build_hf_suggestions(
    target: Path,
    generation: str,
    *,
    current_score: float = 0.0,
    cfg: dict[str, Any] | None = None,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    rows = _fetch_hf_suggestion_rows(target, generation, cfg=cfg, limit=limit)
    summarized = [
        _summarize_hf_suggestion(item)
        for item in _annotate_hf_accelerator_rows(target, rows, current_score=current_score)
    ]
    recommended = summarized[0] if summarized and summarized[0].get('is_recommended') else None
    return summarized, recommended


def match_stack_for_target(
    target_path: str | Path,
    *,
    cfg: dict[str, Any] | None = None,
    current_draft_path: str | Path | None = None,
    dflash_generation: str = 'auto',
) -> dict[str, Any]:
    config = cfg or load_config()
    target = Path(str(target_path)).expanduser().resolve()
    if not target.is_file():
        return {'success': False, 'error': 'target file not found'}
    gen_filter = normalize_dflash_generation(dflash_generation, default='auto')
    generation_pick = resolve_recommended_generation(
        target,
        cfg=config,
        current_draft_path=current_draft_path,
    )
    effective_gen = (
        generation_pick['recommended_generation']
        if gen_filter == 'auto'
        else gen_filter
    )
    local = find_local_accelerators(target, cfg=config, dflash_generation=effective_gen)
    current_score, current_row = _annotate_accelerator_rows(
        target,
        local,
        current_draft_path=current_draft_path,
    )
    best_draft = local[0]['path'] if local else ''
    profile = infer_dflash_profile(target, best_draft)
    hf_query = build_hf_search_query(target, dflash_generation=effective_gen)
    hf_suggestions, recommended_hf = _build_hf_suggestions(
        target,
        effective_gen,
        current_score=current_score,
        cfg=config,
    )

    has_better_local = any(bool(row.get('better_than_current')) for row in local)
    best_local = next((row for row in local if row.get('better_than_current')), None)
    if not best_local and local and not current_draft_path:
        best_local = local[0]

    return {
        'success': True,
        'target_path': str(target),
        'target_filename': target.name,
        'target_label': target.stem.replace('_', ' '),
        'local_accelerators': local,
        'current_draft': current_row,
        'current_score': round(current_score, 2),
        'has_better_local': has_better_local,
        'best_local': best_local,
        'has_hf_suggestions': bool(hf_suggestions),
        'hf_query': hf_query,
        'hf_suggestions': hf_suggestions,
        'recommended_hf': recommended_hf,
        'requested_generation': gen_filter,
        'dflash_generation': effective_gen,
        **generation_pick,
        'suggested_profile': profile,
        'suggested_label': suggest_stack_label(target),
        'suggested_model_id': model_id_from_path(target),
        'suggested_server_id': suggest_server_id(target, cfg=config),
        'suggested_port': suggest_server_port(cfg=config),
        'fallback_profile': infer_profile_from_path(target),
    }


def replace_stack_draft(
    server_id: str,
    draft_path: str | Path,
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Swap the draft accelerator on an existing DFlash stack profile."""
    from core.config import get_server, normalize_server, save_config, validate_config
    from core.model_presets import write_server_preset

    config = cfg or load_config()
    key = str(server_id or '').strip()
    if not key:
        return {'success': False, 'error': 'server_id is required'}
    server = get_server(config, key)
    if not server:
        return {'success': False, 'error': f'unknown server: {key}'}

    normalized = normalize_server(server)
    target_path = str(normalized.get('target_path') or '').strip()
    current_draft = str(normalized.get('draft_path') or '').strip()
    if not target_path:
        return {'success': False, 'error': 'server has no target model'}
    if not current_draft:
        return {'success': False, 'error': 'server is not a DFlash stack'}

    target = Path(target_path).expanduser().resolve()
    draft = Path(str(draft_path)).expanduser().resolve()
    if not draft.is_file():
        return {'success': False, 'error': 'accelerator file not found'}
    if not is_accelerator_path(draft):
        return {'success': False, 'error': 'chosen file is not a DFlash accelerator'}
    pair_score = score_accelerator_pair(target, draft)
    if not is_viable_stack_pair(target, draft, pair_score):
        return {
            'success': False,
            'error': 'accelerator does not match this target strongly enough',
            'score': round(pair_score, 2),
        }

    if _resolved_path_key(current_draft) == _resolved_path_key(draft):
        return {
            'success': True,
            'unchanged': True,
            'server': normalized,
            'score': round(pair_score, 2),
        }

    merged = normalize_server({
        **normalized,
        'draft_path': str(draft),
        'profile': infer_dflash_profile(target, draft),
    })
    if not str(merged.get('model_id') or '').strip():
        merged['model_id'] = model_id_from_path(target)
    servers = config.get('servers') or []
    updated = False
    for idx, entry in enumerate(servers):
        if not isinstance(entry, dict) or str(entry.get('id') or '') != key:
            continue
        servers[idx] = merged
        updated = True
        break
    if not updated:
        return {'success': False, 'error': f'unknown server: {key}'}
    config['servers'] = servers
    validate_config(config)
    write_server_preset(merged, cfg=config)
    save_config(config)
    return {
        'success': True,
        'server': merged,
        'score': round(pair_score, 2),
        'previous_draft_path': current_draft,
    }
