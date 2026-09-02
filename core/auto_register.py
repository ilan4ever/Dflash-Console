"""Auto-register GGUF models found in the Console's own model library.

The model catalog shows every GGUF physically present under the Console's
models folder, but a model only becomes a loadable "server" once a profile
exists in ``config.json``. This module bridges that gap:

* On server startup (and on demand via ``POST /api/models/auto-setup``) it
  scans model folders, upgrades plain profiles to DFlash stacks when a
  matching accelerator exists, registers new stacks/targets, and wires local
  vision projectors.
* DFlash-capable targets with a viable local accelerator become target+draft
  stack servers; plain LLM/embedding GGUFs become regular llama-server
  profiles only when no accelerator matches.
* Draft accelerators, vision projectors, split-shard continuation parts and
  STT/TTS weights (managed via ``runtimes[]``) are skipped.

The scan is idempotent. Set ``"auto_register_models": false`` in
``config.json`` to disable it.
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
from core.local_models import list_local_models
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


def _registered_stack_target_paths(cfg: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for server in list_servers(cfg):
        target_path = str(server.get('target_path') or '').strip()
        draft_path = str(server.get('draft_path') or '').strip()
        if target_path and draft_path:
            paths.add(_resolved_key(target_path))
    return paths


def _resolve_draft_for_target(
    target: Path,
    *,
    cfg: dict[str, Any],
    catalog_models: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the best local DFlash accelerator for a target GGUF."""
    from core.stack_match import list_capable_targets

    local = find_local_accelerators(target, cfg=cfg, limit=5)
    for row in local:
        accel_path = str(row.get('path') or '').strip()
        score = float(row.get('score') or 0)
        if accel_path and is_viable_stack_pair(target, accel_path, score):
            return row
    capable = (
        list_capable_targets(cfg=cfg, models=catalog_models)
        if catalog_models is not None
        else list_capable_targets(cfg=cfg)
    )
    target_key = _resolved_key(target)
    for row in capable.get('targets') or []:
        if _resolved_key(str(row.get('path') or '')) != target_key:
            continue
        draft_path = str(row.get('draft_path') or '').strip()
        score = float(row.get('match_score') or 0)
        if draft_path and is_viable_stack_pair(target, draft_path, score):
            return {
                'path': draft_path,
                'filename': row.get('draft_filename') or Path(draft_path).name,
                'score': score,
            }
    return None


def _upgrade_server_entry(
    entry: dict[str, Any],
    *,
    cfg: dict[str, Any],
    catalog_models: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Promote one plain server profile to a DFlash stack when a draft is available."""
    normalized = normalize_server(entry)
    if normalized.get('enabled') is False:
        return None
    if str(normalized.get('engine_mode') or '').strip().lower() == 'embedding':
        return None
    target_path = str(normalized.get('target_path') or '').strip()
    draft_path = str(normalized.get('draft_path') or '').strip()
    if not target_path or draft_path:
        return None
    target = Path(target_path).expanduser()
    if not target.is_file() or not is_target_candidate(target):
        return None
    best = _resolve_draft_for_target(target, cfg=cfg, catalog_models=catalog_models)
    if not best:
        return None
    accel_path = str(best.get('path') or '').strip()
    if not accel_path:
        return None
    draft = Path(accel_path)
    return {
        **normalized,
        'draft_path': str(draft.resolve()),
        'profile': infer_dflash_profile(target, draft),
        'label': str(normalized.get('label') or suggest_stack_label(target)).strip() or suggest_stack_label(target),
    }


def _upgrade_plain_profiles_to_stacks(
    cfg: dict[str, Any],
    *,
    catalog_models: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Promote plain target-only engine profiles when a DFlash accelerator is available."""
    upgraded: list[dict[str, Any]] = []
    servers = cfg.get('servers')
    if not isinstance(servers, list):
        return upgraded
    changed = False
    for index, server in enumerate(servers):
        if not isinstance(server, dict):
            continue
        entry = normalize_server(server)
        if entry.get('enabled') is False:
            continue
        if str(entry.get('engine_mode') or '').strip().lower() == 'embedding':
            continue
        target_path = str(entry.get('target_path') or '').strip()
        draft_path = str(entry.get('draft_path') or '').strip()
        if not target_path or draft_path:
            continue
        target = Path(target_path).expanduser()
        if not target.is_file() or not is_target_candidate(target):
            continue
        merged = _upgrade_server_entry(entry, cfg=cfg, catalog_models=catalog_models)
        if not merged:
            continue
        servers[index] = merged
        write_server_preset(merged, cfg=cfg)
        changed = True
        upgraded.append({
            'server_id': str(merged.get('id') or ''),
            'target_path': str(target.resolve()),
            'draft_path': str(merged.get('draft_path') or ''),
            'action': 'upgraded_plain_to_stack',
        })
    if changed:
        validate_config(cfg)
        save_config(cfg)
    return upgraded


def _register_unregistered_capable_stacks(
    cfg: dict[str, Any],
    *,
    catalog_models: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Create engine profiles for target+accelerator pairs discovered on disk."""
    from core.stack_match import list_capable_targets

    registered: list[dict[str, Any]] = []
    stack_targets = _registered_stack_target_paths(cfg)
    known_paths = _registered_model_paths(cfg)
    if catalog_models is None:
        catalog = list_local_models(cfg=cfg, scan_disk=True, force_refresh=False, include_dflash_stacks=False)
        catalog_models = list(catalog.get('models') or [])
    capable = list_capable_targets(cfg=cfg, models=catalog_models)
    for target in capable.get('targets') or []:
        target_path = str(target.get('path') or '').strip()
        draft_path = str(target.get('draft_path') or '').strip()
        if not target_path or not draft_path:
            continue
        key = _resolved_key(target_path)
        if key in stack_targets:
            continue
        path = Path(target_path).expanduser()
        if not path.is_file():
            continue
        if key in known_paths:
            for index, server in enumerate(cfg.get('servers') or []):
                if not isinstance(server, dict):
                    continue
                if _resolved_key(str(server.get('target_path') or '')) != key:
                    continue
                if str(server.get('draft_path') or '').strip():
                    break
                merged = _upgrade_server_entry(server, cfg=cfg, catalog_models=catalog_models)
                if not merged:
                    break
                servers = cfg.get('servers')
                if isinstance(servers, list):
                    servers[index] = merged
                    write_server_preset(merged, cfg=cfg)
                    validate_config(cfg)
                    save_config(cfg)
                    registered.append({
                        'server_id': merged.get('id'),
                        'label': merged.get('label'),
                        'path': target_path,
                        'draft_path': merged.get('draft_path'),
                        'kind': 'dflash-stack',
                        'action': 'upgraded_plain_to_stack',
                    })
                    stack_targets.add(key)
                break
            continue
        score = float(target.get('match_score') or 0)
        if not is_viable_stack_pair(path, draft_path, score):
            continue
        entry = _register_stack(cfg, path, {'path': draft_path, 'filename': target.get('draft_filename')})
        registered.append({
            'server_id': entry.get('id'),
            'label': entry.get('label'),
            'path': target_path,
            'draft_path': draft_path,
            'kind': 'dflash-stack',
            'action': 'registered_capable_stack',
        })
        stack_targets.add(key)
        known_paths.add(key)
    return registered


def _wire_local_vision_for_servers(cfg: dict[str, Any], *, download: bool = False) -> list[dict[str, Any]]:
    """Attach a local mmproj when one already sits next to the target GGUF."""
    from core.vision_setup import resolve_mmproj_path, wire_vision

    results: list[dict[str, Any]] = []
    for server in list_servers(cfg):
        if not isinstance(server, dict) or server.get('enabled') is False:
            continue
        target_path = str(server.get('target_path') or '').strip()
        if not target_path:
            continue
        mmproj = str(resolve_mmproj_path(server, cfg=cfg) or '').strip()
        if not mmproj or not Path(mmproj).is_file():
            continue
        explicit = str(server.get('mmproj_path') or '').strip()
        if explicit and _resolved_key(explicit) == _resolved_key(mmproj):
            continue
        wired = wire_vision(
            model_path=target_path,
            mmproj_path=mmproj,
            server_id=str(server.get('id') or '').strip() or None,
            cfg=cfg,
        )
        if wired.get('success') or wired.get('vision_ready'):
            results.append({
                'server_id': str(server.get('id') or ''),
                'mmproj_path': wired.get('mmproj_path') or mmproj,
                'action': 'vision_wired',
            })
    return results


def ensure_stack_for_pair(
    target_path: str,
    draft_path: str,
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the engine profile for this target+draft, creating or upgrading it."""
    config = cfg if cfg is not None else load_config()
    target = Path(str(target_path or '')).expanduser()
    draft = Path(str(draft_path or '')).expanduser()
    if not target.is_file():
        return {'success': False, 'error': f'target model not found: {target}'}
    if not draft.is_file():
        return {'success': False, 'error': f'accelerator not found: {draft}'}
    if not is_target_candidate(target):
        return {'success': False, 'error': 'Choose a full target GGUF, not a DFlash accelerator.'}
    if not is_accelerator_path(draft):
        return {'success': False, 'error': 'The accelerator file must include DFlash in its filename.'}
    from core.stack_match import preflight_dflash_pair, score_accelerator_pair

    pair_score = score_accelerator_pair(target, draft)
    preflight = preflight_dflash_pair(target, draft, score=pair_score)
    if not preflight.get('compatible'):
        return {
            'success': False,
            'error': str(preflight.get('reason') or 'target and accelerator are incompatible'),
            'reason_code': preflight.get('reason_code'),
            'preflight': preflight,
        }
    if preflight.get('reason_code') != 'metadata-unavailable' and not preflight.get('validated'):
        return {
            'success': False,
            'error': 'Both GGUF files must pass compatibility preflight before they can be registered.',
            'reason_code': 'preflight-unavailable',
            'preflight': preflight,
        }
    target_key = _resolved_key(target)
    draft_resolved = str(draft.resolve())
    for index, server in enumerate(config.get('servers') or []):
        if not isinstance(server, dict):
            continue
        entry = normalize_server(server)
        existing_target = str(entry.get('target_path') or '').strip()
        if existing_target and _resolved_key(existing_target) != target_key:
            continue
        if not existing_target:
            continue
        merged = {
            **entry,
            'draft_path': draft_resolved,
            'profile': infer_dflash_profile(target, draft),
            'label': str(entry.get('label') or suggest_stack_label(target)).strip()
            or suggest_stack_label(target),
        }
        servers = config.get('servers')
        if isinstance(servers, list):
            servers[index] = merged
            write_server_preset(merged, cfg=config)
            validate_config(config)
            save_config(config)
        from core.local_models import invalidate_model_catalog_cache

        invalidate_model_catalog_cache()
        return {
            'success': True,
            'created': False,
            'server': merged,
            'server_id': str(merged.get('id') or ''),
            'preflight': preflight,
        }
    entry = _register_stack(config, target, {'path': draft_resolved, 'filename': draft.name})
    from core.local_models import invalidate_model_catalog_cache

    invalidate_model_catalog_cache()
    return {
        'success': True,
        'created': True,
        'server': entry,
        'server_id': str(entry.get('id') or ''),
        'preflight': preflight,
    }


def auto_setup_models(
    *,
    cfg: dict[str, Any] | None = None,
    download_vision: bool = False,
    catalog_models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Upgrade plain profiles, register stacks, scan the library, and wire vision."""
    config = cfg if cfg is not None else load_config()
    if config.get('auto_register_models', True) is False:
        return {
            'success': True,
            'enabled': False,
            'upgraded': [],
            'registered': [],
            'stacks': [],
            'vision': [],
            'skipped': [],
            'already_registered': 0,
        }

    upgraded = _upgrade_plain_profiles_to_stacks(config, catalog_models=catalog_models)
    register_result = auto_register_console_models(cfg=config)
    stacks = _register_unregistered_capable_stacks(config, catalog_models=catalog_models)
    vision = _wire_local_vision_for_servers(config, download=download_vision)
    changed = bool(upgraded or stacks or vision or register_result.get('registered'))
    if changed:
        from core.local_models import invalidate_model_catalog_cache

        invalidate_model_catalog_cache()
    return {
        'success': True,
        'enabled': True,
        'changed': changed,
        'upgraded': upgraded,
        'registered': register_result.get('registered') or [],
        'stacks': stacks,
        'vision': vision,
        'skipped': register_result.get('skipped') or [],
        'already_registered': int(register_result.get('already_registered') or 0),
    }


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
