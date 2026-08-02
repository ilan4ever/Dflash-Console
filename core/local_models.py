"""Scan local GGUF models and map them to DFlash Console server profiles."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.config import list_servers, load_config, normalize_server
from core.model_paths import enabled_scan_roots, get_download_dir, get_model_libraries, storage_presets
from core.model_stack import resolve_model_stack

_CATALOG_CACHE: dict[str, Any] | None = None
_CATALOG_CACHE_AT: float = 0.0
_CATALOG_TTL_SECONDS = 120.0

_QUANT_RE = re.compile(r'Q\d[_A-Z0-9]+', re.I)
_PARAM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*[Bb]', re.I)


def _guess_arch(name: str) -> str:
    lower = name.lower()
    if 'gemma' in lower:
        return 'gemma4'
    if 'qwen' in lower:
        return 'qwen'
    if 'deepseek' in lower:
        return 'deepseekv2'
    if 'bonsai' in lower:
        return 'bonsai'
    return 'unknown'


def _guess_params(name: str) -> str:
    match = _PARAM_RE.search(name)
    if match:
        return f"{match.group(1)}B"
    return '—'


def _guess_quant(name: str) -> str:
    match = _QUANT_RE.search(name)
    return match.group(0).upper() if match else '—'


def _publisher(path: Path) -> str:
    parts = path.parts
    try:
        idx = parts.index('models')
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    return path.parent.name


def _modified_label(path: Path) -> str:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return '—'
    if age < 86400:
        return 'today'
    if age < 86400 * 2:
        return '1 day ago'
    if age < 86400 * 7:
        return f"{int(age / 86400)} days ago"
    if age < 86400 * 30:
        return f"{int(age / (86400 * 7))} weeks ago"
    return f"{int(age / (86400 * 30))} months ago"


def _size_gb(path: Path) -> float | None:
    try:
        return round(path.stat().st_size / (1024 ** 3), 2)
    except OSError:
        return None


def _has_vision_support(path: Path | None) -> bool:
    """True when a multimodal projector (mmproj) or VL naming indicates vision."""
    from core.vision_setup import _is_mmproj_name

    if not path or not path.is_file():
        return False
    lower = path.name.lower()
    if any(token in lower for token in ('-vl-', '_vl_', 'vision', 'multimodal')):
        return True
    try:
        for sibling in path.parent.glob('*.gguf'):
            if _is_mmproj_name(sibling.name):
                return True
    except OSError:
        pass
    return False


def _append_vision_capability(caps: list[str], path: Path | None, *, mmproj_path: str | None = None) -> None:
    if 'vision' in caps:
        return
    if _has_vision_support(path):
        caps.append('vision')
        return
    explicit = str(mmproj_path or '').strip()
    if explicit and Path(explicit).is_file():
        caps.append('vision')


def _scan_gguf(root: Path, *, source: str) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob('*.gguf')):
        name = path.name
        if name.lower().startswith('mmproj'):
            continue
        row = {
            'id': path.stem.replace('_', '-').lower()[:120],
            'path': str(path),
            'filename': name,
            'arch': _guess_arch(name),
            'params': _guess_params(name),
            'publisher': _publisher(path),
            'quant': _guess_quant(name),
            'size_gb': _size_gb(path),
            'modified': _modified_label(path),
            'source': source,
            'capabilities': [],
        }
        caps = row['capabilities']
        _append_vision_capability(caps, path)
        rows.append(row)
    return rows


def _resolve_stack_pair(server: dict[str, Any], *, cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    stack = resolve_model_stack(server, cfg=cfg)
    target = next((row for row in stack if row.get('role') == 'target'), None)
    draft = next((row for row in stack if str(row.get('role') or '').startswith('draft')), None)
    return target, draft


def _server_catalog_row(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any],
    enabled: bool | None = None,
) -> dict[str, Any]:
    target, draft = _resolve_stack_pair(server, cfg=cfg)
    path = Path(str(target.get('path') or '')) if target else None
    caps = ['instruct']
    profile = str(server.get('profile') or '')
    if draft:
        caps.append('dflash')
    elif profile == 'gemma-12-ar':
        caps.append('ar')
    else:
        caps.append('ar')
    if 'gemma' in str(server.get('model_id') or '').lower():
        caps.append('tools')
    from core.vision_setup import resolve_mmproj_path

    _append_vision_capability(caps, path, mmproj_path=resolve_mmproj_path(server, cfg=cfg))
    draft_path = str(draft.get('path') or '') if draft else ''
    draft_path_obj = Path(draft_path) if draft_path else None
    is_enabled = enabled if enabled is not None else server.get('enabled', True) is not False
    has_dflash = 'dflash' in caps
    target_ready = bool(path and path.is_file())
    draft_ready = bool(draft_path_obj and draft_path_obj.is_file())
    return {
        'id': str(server.get('model_id') or server.get('id')),
        'server_id': str(server.get('id') or ''),
        'label': str(server.get('label') or server.get('model_id') or ''),
        'profile': str(server.get('profile') or ''),
        'port': int(server.get('port') or 0),
        'loadable': is_enabled and has_dflash and target_ready and draft_ready,
        'path': str(target.get('path') or '') if target else '',
        'filename': path.name if path and path.name else '',
        'arch': _guess_arch(str(server.get('label') or path.name if path else '')),
        'params': _guess_params(str(server.get('label') or path.name if path else '')),
        'publisher': _publisher(path) if path else 'dflash',
        'quant': _guess_quant(path.name if path else str(server.get('label') or '')),
        'size_gb': target.get('size_gb') if target else _size_gb(path) if path else None,
        'modified': _modified_label(path) if path and path.is_file() else '—',
        'source': 'dflash-profile',
        'capabilities': caps,
        'context_max': _context_max_for_profile(str(server.get('profile') or '')),
        'draft_label': draft.get('label') if draft else '',
        'draft_path': draft_path,
        'draft_filename': draft_path_obj.name if draft_path_obj else '',
        'draft_size_gb': _size_gb(draft_path_obj) if draft_path_obj and draft_path_obj.is_file() else None,
        'draft_quant': _guess_quant(draft_path_obj.name) if draft_path_obj else '',
        'load_settings': server.get('load_settings') or {},
        'inference_settings': server.get('inference_settings') or {},
        'context_size': server.get('context_size'),
        'gpu_layers_max': 128,
        'dflash_stack': has_dflash,
        'stack_status': 'ready' if is_enabled and has_dflash else ('disabled' if has_dflash else ''),
    }


def _normalize_path_key(path_text: str) -> str:
    text = str(path_text or '').strip()
    if not text:
        return ''
    try:
        return str(Path(text).resolve()).lower()
    except OSError:
        return text.lower()


def _registered_stack_targets(config: dict[str, Any], *, cfg: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for server in list_servers(config):
        target, draft = _resolve_stack_pair(normalize_server(server), cfg=cfg)
        if not draft or not target:
            continue
        path_key = _normalize_path_key(str(target.get('path') or ''))
        if path_key:
            paths.add(path_key)
    return paths


def _capable_stack_row(target: dict[str, Any]) -> dict[str, Any]:
    from core.stack_match import suggest_stack_label

    path = Path(str(target.get('path') or ''))
    draft_path = str(target.get('draft_path') or '').strip()
    draft_path_obj = Path(draft_path) if draft_path else None
    label = suggest_stack_label(path) if path.is_file() else str(target.get('label') or path.name)
    caps = ['instruct', 'dflash']
    _append_vision_capability(caps, path)
    return {
        'id': f"stack-capable:{path.stem.replace('_', '-').lower()[:96]}",
        'server_id': '',
        'label': label,
        'profile': '',
        'port': 0,
        'loadable': path.is_file(),
        'path': str(path),
        'filename': path.name,
        'arch': target.get('arch') or _guess_arch(path.name),
        'params': target.get('params') or _guess_params(path.name),
        'publisher': target.get('publisher') or _publisher(path),
        'quant': target.get('quant') or _guess_quant(path.name),
        'size_gb': target.get('size_gb'),
        'modified': target.get('modified') or (_modified_label(path) if path.is_file() else '—'),
        'source': 'dflash-stack',
        'capabilities': caps,
        'context_max': 131072,
        'draft_label': target.get('draft_filename') or '',
        'draft_path': draft_path,
        'draft_filename': draft_path_obj.name if draft_path_obj else str(target.get('draft_filename') or ''),
        'draft_size_gb': target.get('draft_size_gb'),
        'draft_quant': _guess_quant(draft_path_obj.name) if draft_path_obj else '',
        'load_settings': {},
        'inference_settings': {},
        'context_size': 8192,
        'gpu_layers_max': 128,
        'dflash_stack': True,
        'stack_status': 'unregistered',
        'match_score': target.get('match_score'),
    }


def _dflash_stack_supplement(config: dict[str, Any], catalog: dict[str, dict[str, Any]], *, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from core.stack_match import list_capable_targets

    rows: list[dict[str, Any]] = []
    registered_targets = _registered_stack_targets(config, cfg=cfg)
    catalog_server_ids = {str(row.get('server_id') or '') for row in catalog.values()}

    for server in list_servers(config):
        if server.get('enabled', True) is not False:
            continue
        normalized = normalize_server(server)
        server_id = str(normalized.get('id') or '')
        if server_id in catalog_server_ids:
            continue
        _, draft = _resolve_stack_pair(normalized, cfg=cfg)
        if not draft:
            continue
        rows.append(_server_catalog_row(normalized, cfg=cfg, enabled=False))

    seen_targets = set(registered_targets)
    for target in list_capable_targets(cfg=config).get('targets') or []:
        path_key = _normalize_path_key(str(target.get('path') or ''))
        if not path_key or path_key in seen_targets:
            continue
        seen_targets.add(path_key)
        rows.append(_capable_stack_row(target))

    return rows


def _mark_duplicate_files(models: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in models:
        path_text = str(row.get('path') or '').strip()
        if not path_text:
            continue
        name = str(row.get('filename') or Path(path_text).name).lower()
        if not name:
            continue
        groups[name].append(row)
    for name, group in groups.items():
        if len(group) < 2:
            continue
        group_id = f'dup:{name}'
        paths = [str(item.get('path') or '') for item in group]
        for row in group:
            row['duplicate_group'] = group_id
            row['duplicate_count'] = len(group)
            row['duplicate_paths'] = paths


def _context_max_for_profile(profile: str) -> int:
    if profile in ('gemma-chat', 'gemma-ar', 'gemma-12-ar', 'gemma-12-dflash'):
        return 262144
    if profile in ('qwen-dflash', 'qwen-ar'):
        return 32768
    if profile == 'bonsai-spec':
        return 16384
    if profile == 'bonsai':
        return 8192
    return 131072


def _profile_catalog(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        row = _server_catalog_row(normalize_server(server), cfg=config)
        catalog[row['server_id']] = row
    return catalog


def _build_models_payload(
    config: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    extras: list[dict[str, Any]],
    *,
    partial: bool = False,
) -> dict[str, Any]:
    models = list(catalog.values()) + sorted(extras, key=lambda r: (r.get('label') or '').lower())
    _mark_duplicate_files(models)
    models.sort(key=lambda r: (0 if r.get('loadable') else 1, (r.get('label') or '').lower()))
    total_gb = round(sum(float(r.get('size_gb') or 0) for r in models), 2)
    loadable_count = sum(1 for r in models if r.get('loadable'))
    libraries = get_model_libraries(config)
    download_dir = get_download_dir(config)
    payload: dict[str, Any] = {
        'success': True,
        'models': models,
        'models_dir': str(download_dir),
        'model_libraries': libraries,
        'storage_presets': storage_presets(),
        'total_count': len(models),
        'total_size_gb': total_gb,
        'loadable_count': loadable_count,
    }
    if partial:
        payload['partial'] = True
    return payload


def invalidate_model_catalog_cache() -> None:
    global _CATALOG_CACHE, _CATALOG_CACHE_AT
    _CATALOG_CACHE = None
    _CATALOG_CACHE_AT = 0.0


def warm_model_catalog(*, cfg: dict[str, Any] | None = None) -> None:
    """Pre-scan local GGUF libraries so the first UI request is fast."""
    list_local_models(cfg=cfg, scan_disk=True, force_refresh=True)


def list_local_models(
    *,
    cfg: dict[str, Any] | None = None,
    scan_disk: bool = True,
    force_refresh: bool = False,
    include_dflash_stacks: bool = True,
) -> dict[str, Any]:
    global _CATALOG_CACHE, _CATALOG_CACHE_AT
    config = cfg or load_config()
    catalog = _profile_catalog(config)

    if not scan_disk:
        return _build_models_payload(config, catalog, [], partial=True)

    now = time.time()
    if (
        include_dflash_stacks
        and not force_refresh
        and _CATALOG_CACHE
        and (now - _CATALOG_CACHE_AT) < _CATALOG_TTL_SECONDS
    ):
        return _CATALOG_CACHE

    scanned: list[dict[str, Any]] = []
    for root, source in enabled_scan_roots(config):
        scanned.extend(_scan_gguf(root, source=source))

    extras: list[dict[str, Any]] = []
    known_paths = {str(row.get('path') or '').lower() for row in catalog.values()}
    for row in scanned:
        if str(row.get('path') or '').lower() in known_paths:
            continue
        caps = list(row.get('capabilities') or [])
        if 'instruct' not in caps:
            caps.insert(0, 'instruct')
        if 'llm' not in caps:
            caps.append('llm')
        row['server_id'] = ''
        row['label'] = row.get('filename') or row.get('id')
        row['profile'] = ''
        row['port'] = 0
        row['loadable'] = True
        row['context_max'] = 131072
        row['context_size'] = 8192
        row['load_settings'] = {}
        row['inference_settings'] = {}
        row['gpu_layers_max'] = 128
        row['capabilities'] = caps
        row['dflash_stack'] = False
        row['stack_status'] = ''
        row['plain_gguf'] = True
        extras.append(row)

    stack_rows = _dflash_stack_supplement(config, catalog, cfg=config) if include_dflash_stacks else []
    payload = _build_models_payload(config, catalog, stack_rows + extras)
    if include_dflash_stacks:
        _CATALOG_CACHE = payload
        _CATALOG_CACHE_AT = now
    return payload
