"""Copy or move model folders into the DFlash library home."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from core.config import load_config
from core.model_discovery import collect_model_files, summarize_library_path
from core.model_paths import _STORAGE_PRESETS, _preset_path

_VALID_MODES = frozenset({'link', 'copy', 'move'})


def _slug_folder(name: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9._-]+', '-', str(name or '').strip()).strip('-')
    return slug[:64] or 'imported-models'


def _unique_dest(parent: Path, name: str) -> Path:
    base = _slug_folder(name)
    candidate = parent / base
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        next_path = parent / f'{base}-{index}'
        if not next_path.exists():
            return next_path
        index += 1


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _cleanup_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        current = Path(dirpath)
        if current == root:
            continue
        try:
            if not any(current.iterdir()):
                current.rmdir()
        except OSError:
            continue
    try:
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass


def _copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


def _move_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    shutil.move(str(source), str(dest))


def import_plan(
    source_path: str,
    *,
    preset: str = 'custom',
    mode: str = 'link',
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    preset_key = str(preset or 'custom').strip().lower()
    mode_key = str(mode or 'link').strip().lower()
    if mode_key not in _VALID_MODES:
        mode_key = 'link'
    source = Path(os.path.expanduser(str(source_path or '').strip())).resolve()
    dest_root = _preset_path(preset_key, config).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    files = collect_model_files(source, preset_key) if source.is_dir() else []
    stats = summarize_library_path(source, preset_key) if source.is_dir() else {'model_count': 0}
    already_home = source.is_dir() and _is_under(source, dest_root)
    suggested_dest = str(source if already_home else _unique_dest(dest_root, source.name if source.is_dir() else 'imported-models'))
    total_bytes = 0
    for path in files:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue
    return {
        'success': True,
        'mode': mode_key,
        'preset': preset_key,
        'source_path': str(source),
        'destination_root': str(dest_root),
        'destination_path': suggested_dest,
        'already_in_library_home': already_home,
        'file_count': len(files),
        'model_count': int(stats.get('model_count') or 0),
        'size_gb': round(total_bytes / (1024 ** 3), 2) if total_bytes else float(stats.get('size_gb') or 0),
        'use_whole_folder': len(files) == 0 and source.is_dir(),
        'destination_label': _STORAGE_PRESETS.get(preset_key, {}).get('label') or preset_key,
    }


def import_library_folder(
    source_path: str,
    *,
    preset: str = 'custom',
    mode: str = 'link',
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    preset_key = str(preset or 'custom').strip().lower()
    mode_key = str(mode or 'link').strip().lower()
    if mode_key not in _VALID_MODES:
        raise ValueError(f'unsupported import mode: {mode}')
    source = Path(os.path.expanduser(str(source_path or '').strip())).resolve()
    if not source.is_dir():
        raise ValueError('source folder does not exist')
    dest_root = _preset_path(preset_key, config).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    already_home = _is_under(source, dest_root)

    if mode_key == 'link' or already_home:
        final_path = source
        applied_mode = 'link'
    else:
        applied_mode = mode_key
        files = collect_model_files(source, preset_key)
        dest_dir = _unique_dest(dest_root, source.name)
        if not files:
            if mode_key == 'copy':
                shutil.copytree(source, dest_dir, dirs_exist_ok=False)
            else:
                shutil.move(str(source), str(dest_dir))
            final_path = dest_dir
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            for file_path in files:
                rel = file_path.relative_to(source)
                target = dest_dir / rel
                if mode_key == 'copy':
                    _copy_file(file_path, target)
                else:
                    _move_file(file_path, target)
            if mode_key == 'move':
                _cleanup_empty_dirs(source)
            final_path = dest_dir

    stats = summarize_library_path(final_path, preset_key)
    label = stats.get('label_hint') or source.name
    return {
        'success': True,
        'mode': applied_mode,
        'source_path': str(source),
        'library_path': str(final_path),
        'library': {
            'path': str(final_path),
            'preset': preset_key,
            'label': label,
            'model_count': stats.get('model_count'),
            'model_type': stats.get('model_type'),
            'sample_models': stats.get('sample_models'),
        },
    }
