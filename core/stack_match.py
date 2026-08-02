"""Match target GGUF checkpoints to DFlash accelerators (local + Hugging Face)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.config import load_config, list_servers, suggest_server_port
from core.local_models import list_local_models
from core.model_presets import infer_profile_from_path, model_id_from_path

_PARAM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*[Bb]', re.I)
_QUANT_RE = re.compile(r'Q\d[_A-Z0-9]+|F16|BF16|IQ\d_[A-Z0-9]+', re.I)
_DFLASH_RE = re.compile(r'dflash|dspark', re.I)
_TOKEN_RE = re.compile(r'[a-z0-9]+')
_MIN_CAPABLE_SCORE = 2.0


def is_accelerator_path(path: str | Path) -> bool:
    text = Path(path).name.lower()
    return bool(_DFLASH_RE.search(text))


def is_target_candidate(path: str | Path) -> bool:
    text = Path(path).name.lower()
    if not text.endswith('.gguf'):
        return False
    if text.startswith('mmproj') or '.mmproj' in text:
        return False
    if 'translategemma' in text or text.startswith('mtp-'):
        return False
    return not is_accelerator_path(path)


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


def build_hf_search_query(target_path: str | Path) -> str:
    stem = Path(target_path).stem
    stem = _QUANT_RE.sub('', stem).strip(' .-_')
    stem = re.sub(r'\s+', ' ', stem.replace('_', ' ').replace('-', ' ')).strip()
    if not stem:
        return 'dflash gguf'
    if 'dflash' not in stem.lower():
        return f'{stem} DFlash gguf'
    return f'{stem} gguf'


def suggest_stack_label(target_path: str | Path) -> str:
    stem = Path(target_path).stem.replace('_', ' ')
    stem = _QUANT_RE.sub('', stem).strip(' .-')
    param = _param_token(stem)
    family = 'Model'
    lower = stem.lower()
    if 'gemma' in lower:
        family = 'Gemma'
    elif 'qwen' in lower:
        family = 'Qwen'
    elif 'deepseek' in lower:
        family = 'DeepSeek'
    elif 'bonsai' in lower:
        family = 'Bonsai'
    if param:
        return f'{family} {param.upper()} DFlash'
    return f'{stem[:48]} DFlash'


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
    if not is_target_candidate(target):
        return {
            **base,
            'reason_code': 'accelerator',
            'reason': 'This is already a DFlash or DSpark accelerator. Choose the full target GGUF instead.',
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


def list_capable_targets(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    catalog = list_local_models(cfg=config, scan_disk=True, force_refresh=False, include_dflash_stacks=False)
    rows: list[dict[str, Any]] = []
    for model in catalog.get('models') or []:
        # Plain scanned GGUFs are marked loadable so they can be sent to an
        # engine, but they are still valid targets for a new DFlash stack.
        if model.get('loadable') and not model.get('plain_gguf'):
            continue
        path_text = str(model.get('path') or '').strip()
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_file() or not is_target_candidate(path):
            continue
        accelerators = find_local_accelerators(path, cfg=config, limit=5)
        if not accelerators:
            continue
        best = accelerators[0]
        best_score = float(best.get('score') or 0)
        if not is_viable_stack_pair(path, best.get('path') or '', best_score):
            continue
        rows.append({
            'path': str(path.resolve()),
            'filename': path.name,
            'label': model.get('label') or path.name,
            'size_gb': model.get('size_gb'),
            'publisher': model.get('publisher'),
            'arch': model.get('arch'),
            'params': model.get('params'),
            'quant': model.get('quant'),
            'modified': model.get('modified'),
            'accelerator_count': len(accelerators),
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


def find_local_accelerators(target_path: str | Path, *, cfg: dict[str, Any] | None = None, limit: int = 12) -> list[dict[str, Any]]:
    config = cfg or load_config()
    target = Path(str(target_path)).expanduser()
    if not target.is_file():
        return []
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
        })
    rows.sort(key=lambda row: (-float(row.get('score') or 0), row.get('filename') or ''))
    return rows[:limit]


def match_stack_for_target(target_path: str | Path, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    target = Path(str(target_path)).expanduser().resolve()
    if not target.is_file():
        return {'success': False, 'error': 'target file not found'}
    local = find_local_accelerators(target, cfg=config)
    best_draft = local[0]['path'] if local else ''
    profile = infer_dflash_profile(target, best_draft)
    hf_query = build_hf_search_query(target)
    hf_suggestions: list[dict[str, Any]] = []
    try:
        from core.huggingface import search_models

        search = search_models(q=hf_query, limit=8, category='dflash')
        for row in search.get('models') or []:
            hf_suggestions.append({
                'id': row.get('id'),
                'title': row.get('title') or row.get('id'),
                'author': row.get('author'),
                'size_label': row.get('size_label'),
                'url': row.get('url'),
            })
    except Exception:
        hf_suggestions = []

    return {
        'success': True,
        'target_path': str(target),
        'target_filename': target.name,
        'target_label': target.stem.replace('_', ' '),
        'local_accelerators': local,
        'hf_query': hf_query,
        'hf_suggestions': hf_suggestions,
        'suggested_profile': profile,
        'suggested_label': suggest_stack_label(target),
        'suggested_model_id': model_id_from_path(target),
        'suggested_server_id': suggest_server_id(target, cfg=config),
        'suggested_port': suggest_server_port(cfg=config),
        'fallback_profile': infer_profile_from_path(target),
    }
