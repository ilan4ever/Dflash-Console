"""Resolve target + draft model stacks for llama-server profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.config import get_dflash_root
from core.model_paths import get_models_root


def _lmstudio_models_dir() -> Path:
    return Path(os.path.expanduser('~')) / '.lmstudio' / 'models'


def _console_models_dir(cfg: dict[str, Any] | None = None) -> Path:
    return get_models_root(cfg)


def _resolve_gemma_draft_path(models: Path, root: Path, filename: str) -> Path:
    """Return the first existing Gemma DFlash draft path, else the preferred location."""
    candidates = [
        models / 'gemma-draft' / filename,
        models / 'google' / 'gemma-draft' / filename,
        root / 'models' / 'gemma-draft' / filename,
        root / 'models' / 'google' / 'gemma-draft' / filename,
    ]
    if filename.lower().startswith('gemma-4-12b'):
        candidates.extend([
            models / 'williamliao' / 'gemma-4-12B-it-DFlash-GGUF' / 'gemma-4-12B-it-DFlash-F16.gguf',
            root / 'models' / 'williamliao' / 'gemma-4-12B-it-DFlash-GGUF' / 'gemma-4-12B-it-DFlash-F16.gguf',
        ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _file_size_gb(path: Path) -> float | None:
    try:
        if path.is_file():
            return round(path.stat().st_size / (1024 ** 3), 2)
    except OSError:
        pass
    return None


def _basename_id(path: Path) -> str:
    stem = path.stem
    return stem.replace('_', '-').lower()[:80]


def _gemma12_target_rank(path: Path) -> tuple[int, str]:
    from core.stack_match import is_target_candidate

    name = path.name.lower()
    rank = 0
    if 'qat' in name:
        rank += 100
    if 'translategemma' in name:
        rank += 1000
    if not is_target_candidate(path):
        rank += 10000
    return (rank, name)


def _find_gemma12_target(*, cfg: dict[str, Any] | None = None) -> Path | None:
    from core.stack_match import is_target_candidate

    root = get_dflash_root(cfg)
    models = _console_models_dir(cfg)
    candidates: list[Path] = [
        models / 'gemma-4-12b-it' / 'gemma-4-12B-it-Q4_K_M.gguf',
        models / 'google' / 'gemma-4-12b-it' / 'gemma-4-12B-it-Q4_K_M.gguf',
        root / 'models' / 'gemma-4-12b-it' / 'gemma-4-12B-it-Q4_K_M.gguf',
        root / 'models' / 'google' / 'gemma-4-12b-it' / 'gemma-4-12B-it-Q4_K_M.gguf',
        _lmstudio_models_dir() / 'bartowski' / 'gemma-4-12B-it-GGUF' / 'gemma-4-12B-it-Q4_K_M.gguf',
        _lmstudio_models_dir() / 'google' / 'gemma-4-12B-it-qat-q4_0-gguf' / 'gemma-4-12B_q4_0-it.gguf',
        _lmstudio_models_dir() / 'google' / 'gemma-4-12b-it-qat-q4_0-gguf' / 'gemma-4-12b-it-qat-q4_0.gguf',
        _lmstudio_models_dir() / 'google' / 'gemma-4-12b-it-qat-q4_0-gguf' / 'gemma-4-12b_q4_0-it.gguf',
    ]
    found: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and is_target_candidate(candidate):
            found.append(candidate)
    google_dir = _lmstudio_models_dir() / 'google'
    if google_dir.is_dir():
        for hit in google_dir.rglob('*.gguf'):
            name = hit.name.lower()
            if '12' in name and 'gemma' in name and is_target_candidate(hit):
                found.append(hit)
    bart_dir = _lmstudio_models_dir() / 'bartowski'
    if bart_dir.is_dir():
        for hit in bart_dir.rglob('*.gguf'):
            name = hit.name.lower()
            if '12' in name and 'gemma' in name and is_target_candidate(hit):
                found.append(hit)
    if not found:
        return None
    found = sorted(set(found), key=_gemma12_target_rank)
    return found[0]


def _discover_qwen_dflash_stack(*, cfg: dict[str, Any] | None = None) -> tuple[Path | None, Path | None]:
    """Return the best installed Qwen target + DFlash draft pair, if any.

    Disk scan only — must not call ``find_local_accelerators`` / ``list_local_models``
    (those resolve stacks and recurse back into this helper).
    """
    from core.stack_match import is_accelerator_path, is_target_candidate, is_viable_stack_pair, score_accelerator_pair

    models = _console_models_dir(cfg)
    root = get_dflash_root(cfg)
    scan_roots: list[Path] = []
    for base in (models, root / 'models', _lmstudio_models_dir()):
        if base.is_dir():
            scan_roots.append(base)

    targets: list[Path] = []
    accelerators: list[Path] = []
    for base in scan_roots:
        for hit in base.rglob('*.gguf'):
            name = hit.name.lower()
            if is_accelerator_path(hit):
                accelerators.append(hit)
            elif is_target_candidate(hit) and 'qwen' in name:
                targets.append(hit)

    best: tuple[Path, Path, float] | None = None
    seen_targets: set[str] = set()
    for target in sorted(set(targets), key=lambda path: path.name.lower()):
        try:
            target_key = str(target.resolve()).lower()
        except OSError:
            target_key = str(target).lower()
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)

        best_accel: Path | None = None
        best_score = 0.0
        for accel in accelerators:
            try:
                if accel.resolve() == target.resolve():
                    continue
            except OSError:
                if str(accel) == str(target):
                    continue
            score = score_accelerator_pair(target, accel)
            if score <= 0 or not is_viable_stack_pair(target, accel, score):
                continue
            if best_accel is None or score > best_score:
                best_accel = accel
                best_score = score

        if best_accel is not None and (best is None or best_score > best[2]):
            best = (target, best_accel, best_score)

    if not best:
        return None, None
    return best[0], best[1]


def _stack_entry(
    *,
    role: str,
    label: str,
    path: Path | None,
    source: str,
    api_id: str = '',
) -> dict[str, Any]:
    resolved = path if path and path.is_file() else None
    size_gb = _file_size_gb(resolved) if resolved else None
    return {
        'role': role,
        'label': label,
        'id': api_id or ( _basename_id(resolved) if resolved else label),
        'path': str(resolved) if resolved else '',
        'path_missing': bool(path and not resolved),
        'source': source,
        'size_gb': size_gb,
    }


def resolve_model_stack(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return ordered model stack for a server profile (target, optional draft, alias)."""
    custom_target = str(server.get('target_path') or '').strip()
    custom_draft = str(server.get('draft_path') or '').strip()
    if custom_target and not Path(custom_target).expanduser().is_file():
        custom_target = ''
    if custom_draft and not Path(custom_draft).expanduser().is_file():
        custom_draft = ''
    alias = str(server.get('model_id') or '').strip()

    if custom_target:
        stack: list[dict[str, Any]] = []
        if alias:
            stack.append(_stack_entry(
                role='alias',
                label='API alias',
                path=None,
                source='api',
                api_id=alias,
            ))
        target_path = Path(custom_target)
        stack.append(_stack_entry(
            role='target',
            label=target_path.name,
            path=target_path,
            source='custom',
        ))
        if custom_draft:
            draft_path = Path(custom_draft)
            role = 'draft-dspark' if 'dspark' in draft_path.name.lower() else 'draft-dflash'
            stack.append(_stack_entry(
                role=role,
                label=draft_path.name,
                path=draft_path,
                source='custom',
            ))
        return stack

    root = get_dflash_root(cfg)
    models = _console_models_dir(cfg)
    profile = str(server.get('profile') or 'gemma-chat').strip()
    alias = str(server.get('model_id') or '').strip()

    gemma_target = models / 'google' / 'gemma-4-31B-it-qat-q4_0-gguf' / 'gemma-4-31B_q4_0-it.gguf'
    if not gemma_target.is_file():
        gemma_target = _lmstudio_models_dir() / 'google' / 'gemma-4-31B-it-qat-q4_0-gguf' / 'gemma-4-31B_q4_0-it.gguf'
    gemma_draft = _resolve_gemma_draft_path(models, root, 'gemma-4-31B-it-DFlash-Q4_K_M.gguf')
    gemma12_draft = _resolve_gemma_draft_path(models, root, 'gemma-4-12B-it-DFlash-Q4_K_M.gguf')
    bonsai_root = root / 'bonsai-27b'
    bonsai_target = bonsai_root / 'models' / 'ternary-gguf' / '27B' / 'Ternary-Bonsai-27B-Q2_0.gguf'
    bonsai_draft = bonsai_root / 'models' / 'ternary-gguf' / '27B' / 'Ternary-Bonsai-27B-dspark-Q4_1.gguf'
    gemma12 = _find_gemma12_target(cfg=cfg)

    if profile in ('gemma-chat', 'gemma-ar'):
        target = _stack_entry(
            role='target',
            label='Gemma 4 31B (target)',
            path=gemma_target,
            source='lmstudio',
        )
        stack = [target]
        if profile == 'gemma-chat':
            stack.append(_stack_entry(
                role='draft-dflash',
                label='Gemma 4 31B DFlash draft',
                path=gemma_draft,
                source='dflash',
            ))
        if alias:
            stack.insert(0, _stack_entry(
                role='alias',
                label='API alias',
                path=None,
                source='api',
                api_id=alias,
            ))
        return stack

    if profile == 'gemma-12-ar':
        stack = [_stack_entry(
            role='target',
            label='Gemma 4 12B (target)',
            path=gemma12,
            source='lmstudio',
        )]
        if alias:
            stack.insert(0, _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias))
        return stack

    if profile == 'gemma-12-dflash':
        stack = [
            _stack_entry(
                role='target',
                label='Gemma 4 12B (target)',
                path=gemma12,
                source='lmstudio',
            ),
            _stack_entry(
                role='draft-dflash',
                label='Gemma 4 12B DFlash draft',
                path=gemma12_draft,
                source='dflash',
            ),
        ]
        if alias:
            stack.insert(0, _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias))
        return stack

    if profile == 'qwen-dflash':
        qwen_target, qwen_draft = _discover_qwen_dflash_stack(cfg=cfg)
        if qwen_target and qwen_draft:
            stack = [
                _stack_entry(
                    role='target',
                    label=qwen_target.name,
                    path=qwen_target,
                    source='library',
                ),
                _stack_entry(
                    role='draft-dflash',
                    label=qwen_draft.name,
                    path=qwen_draft,
                    source='dflash',
                ),
            ]
            if alias:
                stack.insert(0, _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias))
            return stack
        # No installed pair yet — keep alias-only so repair can disable legacy stubs.
        return [
            _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias),
        ] if alias else []

    if profile == 'qwen-ar':
        return [
            _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias),
        ] if alias else []

    if profile == 'bonsai-spec':
        stack = [
            _stack_entry(role='target', label='Bonsai 27B (target)', path=bonsai_target, source='dflash'),
            _stack_entry(role='draft-dspark', label='Bonsai dspark draft', path=bonsai_draft, source='dflash'),
        ]
        if alias:
            stack.insert(0, _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias))
        return stack

    if profile == 'bonsai':
        stack = [_stack_entry(role='target', label='Bonsai 27B (target)', path=bonsai_target, source='dflash')]
        if alias:
            stack.insert(0, _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias))
        return stack

    if profile == 'generic-ar':
        return [
            _stack_entry(
                role='alias',
                label='API alias',
                path=None,
                source='api',
                api_id=alias,
            ),
        ] if alias else []

    if profile == 'nomic-embed':
        embed_path = _resolve_nomic_embed_path(server, cfg=cfg)
        stack = [
            _stack_entry(
                role='target',
                label='Nomic Embed v1.5',
                path=embed_path,
                source='onevoice' if 'onevoice' in str(embed_path).lower() else 'dflash',
            ),
        ]
        if alias:
            stack.insert(0, _stack_entry(role='alias', label='API alias', path=None, source='api', api_id=alias))
        return stack

    return []


def _resolve_nomic_embed_path(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> Path:
    from core.embedding_server import resolve_embedding_model_path

    for item in server.get('model_stack') or []:
        if not isinstance(item, dict) or str(item.get('role') or '') != 'target':
            continue
        path = Path(str(item.get('path') or '')).expanduser()
        if path.is_file():
            return path
    return resolve_embedding_model_path(server, cfg=cfg)
