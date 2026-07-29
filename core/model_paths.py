"""Configurable checkpoint library roots and download targets."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from core.config import get_dflash_root, load_config

_STORAGE_PRESETS: dict[str, dict[str, str]] = {
    'dflash': {
        'label': 'DFlash checkpoints',
        'description': 'Default GGUF library for this console',
    },
    'gguf': {
        'label': 'GGUF checkpoints',
        'description': 'Generic GGUF weight files',
    },
    'lmstudio': {
        'label': 'LM Studio library',
        'description': 'Optional scan of ~/.lmstudio/models',
    },
    'speech': {
        'label': 'Speech-to-text',
        'description': 'Whisper and ASR weights',
    },
    'tts': {
        'label': 'Text-to-speech',
        'description': 'TTS and voice synthesis models',
    },
    'ocr': {
        'label': 'OCR & vision',
        'description': 'Document and image understanding models',
    },
    'embeddings': {
        'label': 'Embeddings',
        'description': 'Retrieval and vector models',
    },
    'custom': {
        'label': 'Custom folder',
        'description': 'Any directory on disk',
    },
}


def storage_presets() -> list[dict[str, str]]:
    return [
        {'id': key, **meta}
        for key, meta in _STORAGE_PRESETS.items()
    ]


def _slug(value: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', str(value or '').strip().lower()).strip('-')
    return slug[:48] or 'library'


def _preset_path(preset: str, cfg: dict[str, Any]) -> Path:
    dflash_root = get_dflash_root(cfg)
    home = Path.home()
    mapping = {
        'dflash': dflash_root / 'models',
        'gguf': dflash_root / 'models',
        'lmstudio': home / '.lmstudio' / 'models',
        'speech': dflash_root / 'models' / 'speech',
        'tts': dflash_root / 'models' / 'tts',
        'ocr': dflash_root / 'models' / 'ocr',
        'embeddings': dflash_root / 'models' / 'embeddings',
    }
    return mapping.get(preset, dflash_root / 'models').expanduser()


def _source_for_preset(preset: str) -> str:
    if preset == 'lmstudio':
        return 'lmstudio'
    if preset == 'dflash':
        return 'dflash'
    return 'library'


def default_model_libraries(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = cfg or load_config()
    root = _preset_path('dflash', config)
    return [{
        'id': 'dflash-checkpoints',
        'label': _STORAGE_PRESETS['dflash']['label'],
        'path': str(root),
        'enabled': True,
        'preset': 'dflash',
        'download_default': True,
    }]


def normalize_model_library(entry: Any, *, cfg: dict[str, Any], index: int) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    preset = str(entry.get('preset') or 'custom').strip().lower()
    if preset not in _STORAGE_PRESETS:
        preset = 'custom'
    label = str(entry.get('label') or _STORAGE_PRESETS[preset]['label']).strip()
    lib_id = str(entry.get('id') or _slug(label or preset)).strip() or f'library-{index}'
    raw_path = str(entry.get('path') or '').strip()
    if raw_path:
        path = Path(os.path.expanduser(raw_path)).resolve()
    else:
        path = _preset_path(preset, cfg).resolve()
    return {
        'id': lib_id,
        'label': label,
        'path': str(path),
        'enabled': entry.get('enabled') is not False,
        'preset': preset,
        'download_default': entry.get('download_default') is True,
    }


def normalize_model_libraries(raw: Any, *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = cfg or load_config()
    if not isinstance(raw, list) or not raw:
        return default_model_libraries(config)
    libraries: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        normalized = normalize_model_library(entry, cfg=config, index=index)
        if normalized:
            libraries.append(normalized)
    if not libraries:
        return default_model_libraries(config)
    deduped: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in libraries:
        key = str(row.get('path') or '').lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduped.append(row)
    libraries = deduped
    if not any(row.get('download_default') for row in libraries):
        for row in libraries:
            if row.get('enabled'):
                row['download_default'] = True
                break
        else:
            libraries[0]['download_default'] = True
    elif sum(1 for row in libraries if row.get('download_default')) > 1:
        seen = False
        for row in libraries:
            if row.get('download_default'):
                if seen:
                    row['download_default'] = False
                else:
                    seen = True
    return libraries


def get_model_libraries(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = cfg or load_config()
    return normalize_model_libraries(config.get('model_libraries'), cfg=config)


def get_download_library(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    libraries = get_model_libraries(cfg)
    for row in libraries:
        if row.get('download_default') and row.get('enabled'):
            return row
    for row in libraries:
        if row.get('enabled'):
            return row
    return libraries[0]


def get_download_dir(cfg: dict[str, Any] | None = None) -> Path:
    library = get_download_library(cfg)
    return Path(str(library.get('path') or '')).expanduser().resolve()


def get_library_by_id(library_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    needle = str(library_id or '').strip()
    if not needle:
        return None
    for row in get_model_libraries(cfg):
        if row.get('id') == needle:
            return row
    return None


def enabled_scan_roots(cfg: dict[str, Any] | None = None) -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for row in get_model_libraries(cfg):
        if not row.get('enabled'):
            continue
        path = Path(str(row.get('path') or '')).expanduser()
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        preset = str(row.get('preset') or 'custom')
        roots.append((path, _source_for_preset(preset)))
    return roots


def allowed_model_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for path, _source in enabled_scan_roots(cfg):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    if not roots:
        roots.append(get_download_dir(cfg))
    return roots
