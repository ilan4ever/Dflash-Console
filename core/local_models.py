"""Scan local GGUF models and map them to DFlash Console server profiles."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from core.config import get_dflash_root, list_servers, load_config, normalize_server
from core.model_paths import enabled_scan_roots, get_download_dir, get_model_libraries, storage_presets
from core.model_stack import resolve_model_stack

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


def _scan_gguf(root: Path, *, source: str) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob('*.gguf')):
        name = path.name
        if name.lower().startswith('mmproj'):
            continue
        if 'dflash' in name.lower() and 'draft' in path.as_posix().lower():
            continue
        rows.append({
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
        })
    return rows


def _server_catalog_row(server: dict[str, Any], *, cfg: dict[str, Any]) -> dict[str, Any]:
    stack = resolve_model_stack(server, cfg=cfg)
    target = next((row for row in stack if row.get('role') == 'target'), None)
    draft = next((row for row in stack if str(row.get('role') or '').startswith('draft')), None)
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
    draft_path = str(draft.get('path') or '') if draft else ''
    draft_path_obj = Path(draft_path) if draft_path else None
    return {
        'id': str(server.get('model_id') or server.get('id')),
        'server_id': str(server.get('id') or ''),
        'label': str(server.get('label') or server.get('model_id') or ''),
        'profile': str(server.get('profile') or ''),
        'port': int(server.get('port') or 0),
        'loadable': True,
        'path': str(target.get('path') or '') if target else '',
        'filename': path.name if path and path.name else '',
        'arch': _guess_arch(str(server.get('label') or path.name if path else '')),
        'params': _guess_params(str(server.get('label') or path.name if path else '')),
        'publisher': _publisher(path) if path else 'dflash',
        'quant': _guess_quant(path.name if path else str(server.get('label') or '')),
        'size_gb': target.get('size_gb') if target else None,
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
    }


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


def list_local_models(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    catalog: dict[str, dict[str, Any]] = {}

    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        row = _server_catalog_row(normalize_server(server), cfg=config)
        catalog[row['server_id']] = row

    scanned: list[dict[str, Any]] = []
    for root, source in enabled_scan_roots(config):
        scanned.extend(_scan_gguf(root, source=source))

    extras: list[dict[str, Any]] = []
    known_paths = {str(row.get('path') or '').lower() for row in catalog.values()}
    for row in scanned:
        if str(row.get('path') or '').lower() in known_paths:
            continue
        row['server_id'] = ''
        row['label'] = row.get('filename') or row.get('id')
        row['profile'] = ''
        row['port'] = 0
        row['loadable'] = False
        row['context_max'] = 131072
        row['context_size'] = 8192
        row['load_settings'] = {}
        row['inference_settings'] = {}
        row['gpu_layers_max'] = 128
        extras.append(row)

    models = list(catalog.values()) + sorted(extras, key=lambda r: (r.get('label') or '').lower())
    models.sort(key=lambda r: (0 if r.get('loadable') else 1, (r.get('label') or '').lower()))
    total_gb = round(sum(float(r.get('size_gb') or 0) for r in models), 2)
    loadable_count = sum(1 for r in models if r.get('loadable'))
    libraries = get_model_libraries(config)
    download_dir = get_download_dir(config)
    return {
        'success': True,
        'models': models,
        'models_dir': str(download_dir),
        'model_libraries': libraries,
        'storage_presets': storage_presets(),
        'total_count': len(models),
        'total_size_gb': total_gb,
        'loadable_count': loadable_count,
    }
