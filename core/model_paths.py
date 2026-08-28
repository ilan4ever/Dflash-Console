"""Configurable checkpoint library roots and download targets."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from core.config import ROOT as CONSOLE_ROOT
from core.config import get_dflash_root, load_config


def get_models_root(cfg: dict[str, Any] | None = None) -> Path:
    """Developer checkout keeps models under the Console app folder."""
    config = cfg if cfg is not None else load_config()
    raw = str(config.get('models_root') or '').strip()
    if raw:
        return Path(raw).expanduser().resolve()
    env = str(os.environ.get('DFLASH_CONSOLE_MODELS') or '').strip()
    if env:
        return Path(env).expanduser().resolve()
    return (CONSOLE_ROOT / 'models').resolve()


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
    models_root = get_models_root(cfg)
    home = Path.home()
    mapping = {
        'dflash': models_root,
        'gguf': models_root,
        'lmstudio': home / '.lmstudio' / 'models',
        'speech': models_root / 'speech',
        'tts': models_root / 'tts',
        'ocr': models_root / 'ocr',
        'embeddings': models_root / 'embeddings',
    }
    return mapping.get(preset, models_root).expanduser()


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


def _source_for_seed_root(path: Path) -> str:
    text = str(path).replace('\\', '/').lower()
    if '/.lmstudio/' in text or text.endswith('/.lmstudio/models'):
        return 'lmstudio'
    if 'huggingface' in text:
        return 'library'
    if 'onevoice' in text:
        return 'library'
    return 'library'


def disk_scan_roots(cfg: dict[str, Any] | None = None) -> list[tuple[Path, str]]:
    """Library roots for All models: enabled libraries plus common on-disk model folders.

    Skips broad home folders and huge caches (Documents/Downloads/HF hub).
    Those stay available via Settings → Scan PC / Add folder.
    """
    config = cfg if cfg is not None else load_config()
    roots = list(enabled_scan_roots(config))
    seen: set[str] = set()
    ordered: list[tuple[Path, str]] = []
    for path, source in roots:
        try:
            key = str(path.expanduser().resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append((path, source))

    home = Path.home()
    local = Path(os.environ.get('LOCALAPPDATA') or '')
    roaming = Path(os.environ.get('APPDATA') or '')
    candidates = [
        get_models_root(config),
        home / '.lmstudio' / 'models',
        local / 'OneVoiceSpeakData' / 'models',
        roaming / 'OneVoice-Speak' / 'models',
        roaming / 'onevoice-speak' / 'models',
        roaming / 'onevoice-speak-dev' / 'models',
        home / 'models',
    ]

    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        if not resolved.is_dir():
            continue
        seen.add(key)
        ordered.append((resolved, _source_for_seed_root(resolved)))
    return ordered


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


def validate_model_path(
    path_text: str,
    *,
    cfg: dict[str, Any] | None = None,
    allowed_extensions: tuple[str, ...] = ('.gguf',),
    allowed_dirs: list[Path] | None = None,
    require_file: bool = True,
) -> Path:
    """Resolve and validate a model path against allowed extensions and roots.

    Used by load/delete endpoints. Extends the old GGUF-only check to cover
    future formats (Piper voices, Whisper folders, ...) without loosening the
    existing root allowlist.

    Raises ``ValueError`` with a clear message when the path is not allowed.
    """
    target = Path(path_text).expanduser().resolve()
    if require_file and not target.is_file():
        raise ValueError('not a GGUF file')
    if target.is_file() and allowed_extensions and target.suffix.lower() not in allowed_extensions:
        raise ValueError(
            'unsupported file type: %s (allowed: %s)'
            % (target.suffix or '(none)', ', '.join(allowed_extensions))
        )
    roots = allowed_dirs if allowed_dirs is not None else allowed_model_roots(cfg)
    if roots and not any(target.is_relative_to(root) for root in roots):
        raise ValueError('path not under allowed model directories')
    return target


def is_deletable_model_path(path: Path) -> bool:
    """True when ``path`` is a single GGUF file or a model directory we manage."""
    try:
        target = path.expanduser().resolve()
    except OSError:
        return False
    if target.suffix.lower() == '.gguf' and target.is_file():
        return True
    if not target.is_dir():
        return False
    if (target / 'config.json').is_file():
        return True
    if (target / 'model.safetensors').is_file() or any(target.glob('model-*.safetensors')):
        return True
    if (target / 'model.bin').is_file():
        return True
    return any(target.glob('*.gguf'))


def validate_deletable_model_path(
    path_text: str,
    *,
    cfg: dict[str, Any] | None = None,
    allowed_dirs: list[Path] | None = None,
) -> Path:
    """Resolve a library model path that may be deleted (file or directory)."""
    target = Path(path_text).expanduser().resolve()
    if not is_deletable_model_path(target):
        raise ValueError('not a deletable model file or directory')
    roots = allowed_dirs if allowed_dirs is not None else allowed_model_roots(cfg)
    if roots and not any(target.is_relative_to(root) for root in roots):
        raise ValueError('path not under allowed model directories')
    return target
