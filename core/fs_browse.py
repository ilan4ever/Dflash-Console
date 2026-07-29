"""Safe filesystem browsing for manual library folder picking."""

from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any

from core.config import get_dflash_root, load_config

_HOME = Path.home()
_THIS_PC = ''


def _resolve_path(raw: str) -> Path | None:
    text = str(raw or '').strip()
    if not text:
        return None
    expanded = Path(os.path.expanduser(text))
    if expanded.is_absolute():
        return expanded.resolve()
    return (_HOME / expanded).resolve()


def _is_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    home = _HOME.resolve()
    if str(resolved).lower().startswith(str(home).lower()):
        return True
    if resolved.anchor:
        try:
            return Path(resolved.anchor).exists()
        except OSError:
            return False
    return False


def _is_drive_root(path: Path) -> bool:
    if os.name != 'nt':
        return path == Path('/')
    drive = path.drive
    if not drive:
        return False
    try:
        return path.resolve() == Path(f'{drive}\\').resolve()
    except OSError:
        return False


def _list_drives() -> list[dict[str, str]]:
    drives: list[dict[str, str]] = []
    if os.name == 'nt':
        try:
            import ctypes

            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for index, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << index):
                    root = f'{letter}:\\'
                    if Path(root).exists():
                        drives.append({'label': f'{letter}:', 'path': root})
        except OSError:
            for letter in string.ascii_uppercase:
                root = f'{letter}:\\'
                if Path(root).exists():
                    drives.append({'label': f'{letter}:', 'path': root})
    elif Path('/').exists():
        drives.append({'label': '/', 'path': '/'})
    return drives


def _model_shortcuts(cfg: dict[str, Any]) -> list[dict[str, str]]:
    home = Path.home()
    dflash_root = get_dflash_root(cfg)
    candidates = [
        ('DFlash models', dflash_root / 'models'),
        ('Hugging Face cache', home / '.cache' / 'huggingface'),
        ('Ollama models', home / '.ollama' / 'models'),
        ('Local models folder', home / 'models'),
    ]
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, path in candidates:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        items.append({'label': label, 'path': str(resolved)})
    return items


def _existing_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in items:
        path = str(row.get('path') or '').strip()
        key = path.lower()
        if not path or key in seen:
            continue
        if path == _THIS_PC or Path(path).exists():
            seen.add(key)
            rows.append({'label': str(row.get('label') or path), 'path': path})
    return rows


def browse_roots(*, preset: str = 'custom', cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = cfg or load_config()
    _ = preset
    return [
        {
            'id': 'drives',
            'label': 'Drives',
            'items': _existing_items([{'label': 'This PC', 'path': _THIS_PC}, *_list_drives()]),
        },
        {
            'id': 'places',
            'label': 'Quick places',
            'items': _existing_items([
                {'label': 'Home', 'path': str(_HOME)},
                {'label': 'Documents', 'path': str(_HOME / 'Documents')},
                {'label': 'Downloads', 'path': str(_HOME / 'Downloads')},
            ]),
        },
        {
            'id': 'shortcuts',
            'label': 'Common model folders',
            'items': _model_shortcuts(config),
        },
    ]


def _browse_this_pc(*, preset_key: str, config: dict[str, Any]) -> dict[str, Any]:
    drives = _list_drives()
    return {
        'success': True,
        'path': _THIS_PC,
        'path_label': 'This PC',
        'parent': '',
        'preset': preset_key,
        'view': 'drives',
        'entries': [
            {'name': row['label'], 'path': row['path'], 'kind': 'drive'}
            for row in drives
        ],
        'quick_roots': browse_roots(preset=preset_key, cfg=config),
    }


def _parent_path(current: Path) -> str:
    if _is_drive_root(current):
        return _THIS_PC
    parent = current.parent
    if parent == current or not _is_allowed(parent):
        return _THIS_PC if not _is_drive_root(current) else ''
    return str(parent)


def browse_directory(raw_path: str = '', *, preset: str = 'custom', cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    preset_key = str(preset or 'custom').strip().lower()
    resolved = _resolve_path(raw_path)
    if not resolved:
        return _browse_this_pc(preset_key=preset_key, config=config)

    current = resolved
    if not _is_allowed(current):
        return _browse_this_pc(preset_key=preset_key, config=config)
    if not current.exists():
        return _browse_this_pc(preset_key=preset_key, config=config)
    if current.is_file():
        current = current.parent

    parent = _parent_path(current)
    entries: list[dict[str, Any]] = []
    try:
        for entry in sorted(current.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith('.'):
                continue
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            child = entry.resolve()
            if not _is_allowed(child):
                continue
            entries.append({
                'name': entry.name,
                'path': str(child),
                'kind': 'folder',
            })
            if len(entries) >= 200:
                break
    except OSError as exc:
        return {'success': False, 'error': str(exc), 'path': str(current)}

    return {
        'success': True,
        'path': str(current),
        'path_label': str(current),
        'parent': parent,
        'preset': preset_key,
        'view': 'folder',
        'entries': entries,
        'quick_roots': browse_roots(preset=preset_key, cfg=config),
    }
