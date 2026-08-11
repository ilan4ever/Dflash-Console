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


def _console_import_root(preset: str = 'dflash', *, cfg: dict[str, Any] | None = None) -> Path:
    """The Console's own model library folder (where DFlash models live)."""
    config = cfg or load_config()
    root = _preset_path(str(preset or 'dflash').strip().lower() or 'dflash', config)
    return Path(root).expanduser().resolve()


def is_under_console_models(path: str | Path, *, preset: str = 'dflash', cfg: dict[str, Any] | None = None) -> bool:
    """True when a file/folder is already physically inside the Console library."""
    try:
        source = Path(str(path)).expanduser().resolve()
    except OSError:
        return False
    root = _console_import_root(preset, cfg=cfg)
    try:
        source.relative_to(root)
        return True
    except ValueError:
        return False


def is_faster_whisper_dir(path: str | Path) -> bool:
    """True when ``path`` is a directory containing a faster-whisper model.bin."""
    try:
        target = Path(str(path)).expanduser().resolve()
    except OSError:
        return False
    return target.is_dir() and (target / 'model.bin').is_file()


def _faster_whisper_import_name(source: Path) -> str:
    """Friendly folder name for an imported faster-whisper model.

    Uses the HF repo name (``models--<org>--<name>`` ancestor) when available so
    the destination folder carries a whisper marker and a readable label
    (e.g. ``faster-whisper-small.en``) instead of the raw snapshot hash.
    """
    try:
        for part in source.parents:
            if part.name.startswith('models--'):
                bits = part.name.split('--', 2)
                if len(bits) >= 3:
                    return bits[2]
                if len(bits) == 2:
                    return bits[1]
    except OSError:
        pass
    return source.name


def import_single_model_file(
    source_path: str,
    *,
    mode: str = 'copy',
    preset: str = 'dflash',
    folder_name: str | None = None,
    overwrite: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy or move a single external model into the Console's own library.

    Accepts either a single ``.gguf`` file (managed by llama-server) or a
    faster-whisper **model directory** (contains ``model.bin``; managed by the
    faster-whisper STT runtime). ``mode`` is ``copy`` (default) or ``move``.

    The destination folder is deterministic (the repo/friendly name). If a model
    with the same name already exists in the Console library and ``overwrite``
    is false, the import is refused and ``{'success': False, 'exists': True,
    'existing_path': ...}`` is returned so the caller can prompt the user to
    overwrite or abort — instead of silently creating a ``name-2`` duplicate.
    """
    config = cfg or load_config()
    mode_key = str(mode or 'copy').strip().lower()
    if mode_key not in ('copy', 'move'):
        mode_key = 'copy'
    source = Path(os.path.expanduser(str(source_path or '').strip())).resolve()
    is_fw_dir = is_faster_whisper_dir(source)
    if not ((source.is_file() and source.suffix.lower() == '.gguf') or is_fw_dir):
        raise ValueError('source is not a GGUF file or a faster-whisper model directory')
    root = _console_import_root(preset, cfg=config)
    root.mkdir(parents=True, exist_ok=True)

    if is_fw_dir:
        dest_dir = root / (folder_name or _faster_whisper_import_name(source))
        if dest_dir.exists():
            if not overwrite:
                return {
                    'success': False,
                    'exists': True,
                    'existing_path': str(dest_dir),
                    'error': f'{dest_dir.name} already exists in the DFlash Console library',
                }
            shutil.rmtree(dest_dir, ignore_errors=True)
        if mode_key == 'move':
            shutil.move(str(source), str(dest_dir))
        else:
            shutil.copytree(source, dest_dir)
        return {
            'success': True,
            'mode': mode_key,
            'source_path': str(source),
            'library_path': str(dest_dir),
            'preset': preset,
            'runtime_id': 'faster-whisper',
            'model_kind': 'faster-whisper',
        }

    dest_dir = root / (folder_name or source.stem)
    if dest_dir.exists():
        if not overwrite:
            return {
                'success': False,
                'exists': True,
                'existing_path': str(dest_dir),
                'error': f'{dest_dir.name} already exists in the DFlash Console library',
            }
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if mode_key == 'move':
        _move_file(source, dest)
    else:
        _copy_file(source, dest)
    return {
        'success': True,
        'mode': mode_key,
        'source_path': str(source),
        'library_path': str(dest),
        'preset': preset,
    }


def import_stack_pair(
    target_path: str,
    draft_path: str,
    *,
    label: str | None = None,
    preset: str = 'dflash',
    mode: str = 'copy',
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy or move a DFlash stack pair (target + accelerator) into the Console library.

    When a user converts a regular model to a DFlash stack, both the full target
    GGUF and its accelerator are brought into the Console's own models folder so
    the pair is registered under DFlash Console and the originals can be deleted
    elsewhere. ``mode`` is ``copy`` (default, keeps the originals) or ``move``
    (removes the originals after copying). Returns the new paths for registering
    the engine profile.
    """
    config = cfg or load_config()
    mode_key = str(mode or 'copy').strip().lower()
    if mode_key not in ('copy', 'move'):
        mode_key = 'copy'
    target = Path(os.path.expanduser(str(target_path or '').strip())).resolve()
    draft = Path(os.path.expanduser(str(draft_path or '').strip())).resolve()
    if not target.is_file() or target.suffix.lower() != '.gguf':
        raise ValueError('target is not a GGUF file')
    if not draft.is_file() or draft.suffix.lower() != '.gguf':
        raise ValueError('accelerator is not a GGUF file')
    root = _console_import_root(preset, cfg=config)
    root.mkdir(parents=True, exist_ok=True)
    folder = _slug_folder(label or target.stem)
    dest_dir = _unique_dest(root, folder)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_dest = dest_dir / target.name
    draft_dest = dest_dir / draft.name
    if mode_key == 'move':
        _move_file(target, target_dest)
        _move_file(draft, draft_dest)
    else:
        _copy_file(target, target_dest)
        _copy_file(draft, draft_dest)
    return {
        'success': True,
        'mode': mode_key,
        'target_path': str(target_dest),
        'draft_path': str(draft_dest),
        'library_path': str(dest_dir),
        'target_filename': target.name,
        'draft_filename': draft.name,
    }

