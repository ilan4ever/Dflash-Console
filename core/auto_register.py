"""Auto-register GGUF models found in the Console's own model library.

The model catalog shows every GGUF physically present under the Console's
models folder, but a model only becomes a loadable "server" once a profile
exists in ``config.json``. This module bridges that gap:

* On server startup (and on demand via ``POST /api/models/auto-register``) it
  scans the Console's own models root and creates server profiles for every
  model that is not registered yet.
* DFlash-capable targets with a viable local accelerator become target+draft
  stack servers; plain LLM/embedding GGUFs become regular llama-server
  profiles.
* Draft accelerators, vision projectors, split-shard continuation parts and
  STT/TTS weights (managed via ``runtimes[]``) are skipped.

The scan is idempotent: files already referenced by any server are left alone,
and user-created profiles are never modified or removed. Set
``"auto_register_models": false`` in ``config.json`` to disable it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.config import (
    list_servers,
    load_config,
    normalize_server,
    save_config,
    suggest_server_port,
    validate_config,
)
from core.model_paths import get_models_root
from core.model_presets import (
    infer_profile_from_path,
    model_id_from_path,
    write_server_preset,
)
from core.model_stack import resolve_model_stack
from core.stack_match import (
    find_local_accelerators,
    infer_dflash_profile,
    is_accelerator_path,
    is_target_candidate,
    is_viable_stack_pair,
    suggest_server_id,
    suggest_stack_label,
)

_SHARD_RE = re.compile(r'[-_]\d{5}-of-\d{5}', re.I)
_STT_TTS_RE = re.compile(
    r'whisper|speech|asr|parakeet|faster[-_]whisper|piper|kokoro|text.?to.?speech|\btts\b',
    re.I,
)
_EMBED_RE = re.compile(r'embed|nomic|bge[-_]|e5[-_]|gte[-_]', re.I)
_DEFAULT_PLAIN_CONTEXT = 32768


def _resolved_key(path: str | Path) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except OSError:
        return str(Path(path)).lower()


def _registered_model_paths(cfg: dict[str, Any]) -> set[str]:
    """Resolved paths already referenced by any configured server profile."""
    paths: set[str] = set()
    for server in list_servers(cfg):
        try:
            stack = resolve_model_stack(server, cfg=cfg)
        except Exception:
            stack = []
        for row in stack:
            raw = str(row.get('path') or '').strip()
            if raw:
                paths.add(_resolved_key(raw))
    return paths


def _is_split_shard(path: Path) -> bool:
    """True for shard continuation parts (``-00002-of-00003`` etc.)."""
    match = _SHARD_RE.search(path.name)
    if not match:
        return False
    return not path.name[match.start():].startswith('-00001-of-')


def _is_managed_runtime_model(path: Path) -> bool:
    """STT/TTS weights are managed via ``runtimes[]``, not llama-server servers."""
    return bool(_STT_TTS_RE.search(path.name))


def _plain_profile(path: Path) -> str:
    if _EMBED_RE.search(path.name):
        return 'nomic-embed'
    return infer_profile_from_path(path)


def _plain_server_id(cfg: dict[str, Any], path: Path) -> str:
    base = model_id_from_path(path) or Path(path).stem.lower().replace('_', '-')
    base = re.sub(r'[^a-z0-9-]', '-', base.lower())
    base = re.sub(r'-+', '-', base).strip('-')[:48] or 'local-model'
    used = {str(row.get('id') or '') for row in list_servers(cfg)}
    if base not in used:
        return base
    index = 2
    while f'{base}-{index}' in used:
        index += 1
    return f'{base}-{index}'


def _append_server(cfg: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize, validate, persist and preset a newly registered server."""
    servers = cfg.get('servers')
    if not isinstance(servers, list):
        servers = []
    servers.append(entry)
    cfg['servers'] = servers
    validate_config(cfg)
    write_server_preset(entry, cfg=cfg)
    save_config(cfg)
    return entry


def _register_stack(
    cfg: dict[str, Any],
    target: Path,
    draft: dict[str, Any],
) -> dict[str, Any]:
    draft_path = Path(str(draft.get('path') or ''))
    server_id = suggest_server_id(target, cfg=cfg)
    entry = normalize_server({
        'id': server_id,
        'label': suggest_stack_label(target),
        'profile': infer_dflash_profile(target, draft_path),
        'port': suggest_server_port(cfg=cfg),
        'model_id': model_id_from_path(target),
        'target_path': str(target),
        'draft_path': str(draft_path),
        'context_size': _DEFAULT_PLAIN_CONTEXT,
        'enabled': True,
        'engine_on': False,
    })
    return _append_server(cfg, entry)


def _register_plain(cfg: dict[str, Any], target: Path) -> dict[str, Any]:
    entry = normalize_server({
        'id': _plain_server_id(cfg, target),
        'label': Path(target).stem.replace('_', ' ').strip() or 'Local model',
        'profile': _plain_profile(target),
        'port': suggest_server_port(cfg=cfg),
        'model_id': model_id_from_path(target),
        'target_path': str(target),
        'context_size': _DEFAULT_PLAIN_CONTEXT,
        'enabled': True,
        'engine_on': False,
    })
    return _append_server(cfg, entry)


def _registered_target_filenames(cfg: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for server in list_servers(cfg):
        try:
            stack = resolve_model_stack(server, cfg=cfg)
        except Exception:
            stack = []
        for row in stack:
            path_text = str(row.get('path') or '').strip()
            if path_text:
                names.add(Path(path_text).name.lower())
    return names


def auto_register_console_models(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register every unregistered model under the Console's own models root.

    Returns a summary with the newly created server profiles plus the reasons
    files were skipped. Idempotent — never touches already-registered files or
    existing profiles, and only scans the Console's own library folder.
    """
    config = cfg if cfg is not None else load_config()
    if config.get('auto_register_models', True) is False:
        return {
            'success': True,
            'enabled': False,
            'registered': [],
            'skipped': [],
            'already_registered': 0,
        }

    root = get_models_root(config)
    registered = _registered_model_paths(config)
    registered_names = _registered_target_filenames(config)
    results: dict[str, Any] = {
        'success': True,
        'enabled': True,
        'models_root': str(root),
        'registered': [],
        'skipped': [],
        'already_registered': 0,
    }

    if not root.is_dir():
        results['skipped'].append({'path': str(root), 'reason': 'models folder not found'})
        return results

    for path in sorted(root.rglob('*.gguf')):
        key = _resolved_key(path)
        if key in registered:
            results['already_registered'] += 1
            continue
        if path.name.lower() in registered_names:
            results['skipped'].append({
                'path': str(path),
                'reason': 'duplicate filename already registered in Console library',
            })
            continue
        if path.name.lower().startswith('mmproj'):
            continue
        if not is_target_candidate(path):
            results['skipped'].append({
                'path': str(path),
                'reason': 'not a loadable target (projector / mtp / translator / accelerator)',
            })
            continue
        if is_accelerator_path(path):
            results['skipped'].append({
                'path': str(path),
                'reason': 'draft accelerator (stack-only)',
            })
            continue
        if _is_split_shard(path):
            results['skipped'].append({
                'path': str(path),
                'reason': 'split shard continuation part',
            })
            continue
        if _is_managed_runtime_model(path):
            results['skipped'].append({
                'path': str(path),
                'reason': 'STT/TTS weight managed via runtimes',
            })
            continue
        try:
            local = find_local_accelerators(path, cfg=config, limit=5)
            if local and is_viable_stack_pair(
                path,
                local[0].get('path') or '',
                float(local[0].get('score') or 0),
            ):
                entry = _register_stack(config, path, local[0])
                kind = 'dflash-stack'
            else:
                entry = _register_plain(config, path)
                kind = 'llama-server'
            results['registered'].append({
                'server_id': entry.get('id'),
                'label': entry.get('label'),
                'path': str(path),
                'draft_path': entry.get('draft_path'),
                'kind': kind,
            })
            registered.add(key)
            registered_names.add(path.name.lower())
        except Exception as exc:  # noqa: BLE001 - keep the scan resilient
            results['skipped'].append({'path': str(path), 'reason': str(exc)})

    return results
