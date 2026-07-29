"""Discover local model folders on disk (GGUF, Piper, Whisper, OCR, etc.)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from core.config import get_dflash_root, load_config
from core.model_paths import _STORAGE_PRESETS, _preset_path

_SKIP_DIR_NAMES = {
    'node_modules', '.git', '__pycache__', 'venv', '.venv', 'site-packages',
    'windows', 'program files', 'program files (x86)', '$recycle.bin',
    'appdata', 'cache', 'temp', 'tmp', 'dist', 'build', '.cursor',
}
_MAX_SCAN_SECONDS = 12.0
_MAX_DIRS = 4000
_MAX_DEPTH = 6


def _norm(path: Path) -> str:
    try:
        return str(path.expanduser().resolve()).lower()
    except OSError:
        return str(path).lower()


def seed_scan_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    config = cfg or load_config()
    home = Path.home()
    local = os.environ.get('LOCALAPPDATA') or ''
    roaming = os.environ.get('APPDATA') or ''
    dflash = get_dflash_root(config)
    roots: list[Path] = [
        dflash / 'models',
        home / '.lmstudio' / 'models',
        home / '.cache' / 'huggingface',
        Path(local) / 'OneVoiceSpeakData' / 'models',
        Path(roaming) / 'OneVoice-Speak' / 'models',
        Path(roaming) / 'onevoice-speak' / 'models',
        Path(roaming) / 'onevoice-speak-dev' / 'models',
        home / 'Documents',
        home / 'Downloads',
        home / 'models',
    ]
    for preset in ('speech', 'tts', 'ocr', 'embeddings'):
        roots.append(_preset_path(preset, config))
    seen: set[str] = set()
    ordered: list[Path] = []
    for root in roots:
        key = _norm(root)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(root)
    return ordered


def _should_skip_dir(name: str) -> bool:
    lower = name.lower()
    return lower in _SKIP_DIR_NAMES or lower.startswith('.')


def _size_gb(paths: list[Path]) -> float:
    total = 0
    for path in paths[:200]:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return round(total / (1024 ** 3), 2)


def _count_gguf(root: Path, *, max_files: int = 500) -> dict[str, Any]:
    if not root.is_dir():
        return {'count': 0, 'samples': [], 'files': []}
    files: list[Path] = []
    try:
        for path in root.rglob('*.gguf'):
            if len(files) >= max_files:
                break
            if 'mmproj' in path.name.lower():
                continue
            files.append(path)
    except OSError:
        return {'count': 0, 'samples': [], 'files': []}
    samples = [p.name for p in files[:5]]
    return {'count': len(files), 'samples': samples, 'files': files}


def _count_piper_voices(root: Path, *, max_files: int = 300) -> dict[str, Any]:
    if not root.is_dir():
        return {'count': 0, 'samples': [], 'files': []}
    voices: list[str] = []
    files: list[Path] = []
    try:
        for onnx in root.rglob('*.onnx'):
            if len(voices) >= max_files:
                break
            if onnx.name.lower().endswith('.onnx.json'):
                continue
            json_path = onnx.with_suffix('.onnx.json')
            alt_json = Path(str(onnx) + '.json')
            if json_path.is_file() or alt_json.is_file() or 'piper' in onnx.as_posix().lower():
                voice_id = onnx.stem
                voices.append(voice_id)
                files.append(onnx)
    except OSError:
        return {'count': 0, 'samples': [], 'files': []}
    return {'count': len(voices), 'samples': voices[:5], 'files': files}


def _count_whisper(root: Path, *, max_files: int = 200) -> dict[str, Any]:
    if not root.is_dir():
        return {'count': 0, 'samples': [], 'files': []}
    patterns = ('ggml-*.bin', 'ggml-*.gguf', '*whisper*.pt', '*whisper*.bin')
    found: list[Path] = []
    try:
        for pattern in patterns:
            for path in root.rglob(pattern):
                if len(found) >= max_files:
                    break
                found.append(path)
    except OSError:
        return {'count': 0, 'samples': [], 'files': []}
    samples = [p.name for p in found[:5]]
    return {'count': len(found), 'samples': samples, 'files': found}


def _count_hf_checkpoints(root: Path, *, max_dirs: int = 80) -> dict[str, Any]:
    if not root.is_dir():
        return {'count': 0, 'samples': [], 'files': []}
    samples: list[str] = []
    total = 0
    try:
        for path in root.rglob('models--*'):
            if not path.is_dir():
                continue
            total += 1
            if len(samples) < 5:
                samples.append(path.name.replace('models--', '').replace('--', '/'))
            if total >= max_dirs:
                break
    except OSError:
        return {'count': 0, 'samples': [], 'files': []}
    return {'count': total, 'samples': samples, 'files': []}


def _count_ocr(root: Path, *, max_files: int = 120) -> dict[str, Any]:
    if not root.is_dir():
        return {'count': 0, 'samples': [], 'files': []}
    patterns = ('*.traineddata', '*ocr*.onnx', '*paddle*.pdmodel', '*easyocr*')
    found: list[Path] = []
    try:
        for pattern in patterns:
            for path in root.rglob(pattern):
                if len(found) >= max_files:
                    break
                found.append(path)
    except OSError:
        return {'count': 0, 'samples': [], 'files': []}
    return {'count': len(found), 'samples': [p.name for p in found[:5]], 'files': found}


def _count_embeddings(root: Path, *, max_dirs: int = 80) -> dict[str, Any]:
    if not root.is_dir():
        return {'count': 0, 'samples': [], 'files': []}
    hits: list[str] = []
    total = 0
    try:
        for config in root.rglob('config.json'):
            parent = config.parent
            name = parent.name.lower()
            text = parent.as_posix().lower()
            if any(token in text for token in ('embed', 'sentence', 'e5-', 'bge-', 'minilm')):
                total += 1
                if len(hits) < 5:
                    hits.append(parent.name)
            elif 'sentence_transformers' in text or name.startswith('models--'):
                total += 1
                if len(hits) < 5:
                    hits.append(parent.name)
            if total >= max_dirs:
                break
    except OSError:
        return {'count': 0, 'samples': [], 'files': []}
    return {'count': total, 'samples': hits, 'files': []}


_DETECTORS: dict[str, Any] = {
    'gguf': _count_gguf,
    'piper': _count_piper_voices,
    'whisper': _count_whisper,
    'hub': _count_hf_checkpoints,
    'ocr': _count_ocr,
    'embeddings': _count_embeddings,
}

_PRESET_DETECTORS: dict[str, list[str]] = {
    'dflash': ['gguf'],
    'gguf': ['gguf'],
    'lmstudio': ['gguf'],
    'speech': ['whisper', 'hub'],
    'tts': ['piper'],
    'ocr': ['ocr', 'hub'],
    'embeddings': ['embeddings', 'hub'],
    'custom': ['gguf', 'piper', 'whisper', 'hub', 'ocr', 'embeddings'],
}


def _label_for(preset: str, path: Path, model_type: str, count: int, samples: list[str]) -> str:
    if preset == 'lmstudio':
        return 'LM Studio library'
    if model_type == 'piper':
        sample = samples[0] if samples else 'Piper'
        if count == 1:
            return f'Piper · {sample}'
        return f'Piper voices · {count} models'
    if model_type == 'whisper':
        return f'Whisper / speech-to-text · {count} files'
    if model_type == 'hub':
        return f'Hugging Face cache · {count} repos'
    if model_type == 'ocr':
        return f'OCR models · {count} files'
    if model_type == 'embeddings':
        return f'Embedding models · {count} folders'
    if model_type == 'gguf':
        if preset == 'dflash':
            return f'DFlash checkpoints · {count} files'
        if count == 1 and samples:
            return f'GGUF checkpoint · {samples[0]}'
        return f'GGUF checkpoints · {count} files'
    base = _STORAGE_PRESETS.get(preset, {}).get('label') or path.name
    return f'{base} · {count} items'


def summarize_library_path(path: str | Path, preset: str = 'custom') -> dict[str, Any]:
    root = Path(str(path)).expanduser()
    exists = root.is_dir()
    detectors = _PRESET_DETECTORS.get(preset, _PRESET_DETECTORS['custom'])
    best = {'count': 0, 'samples': [], 'model_type': 'unknown', 'files': []}
    for name in detectors:
        fn = _DETECTORS[name]
        result = fn(root)
        if result['count'] > best['count']:
            best = {**result, 'model_type': name}
    count = int(best.get('count') or 0)
    samples = list(best.get('samples') or [])
    model_type = str(best.get('model_type') or 'unknown')
    return {
        'exists': exists,
        'model_count': count,
        'model_type': model_type,
        'sample_models': samples,
        'size_gb': _size_gb(list(best.get('files') or [])),
        'label_hint': _label_for(preset, root, model_type, count, samples) if count else _STORAGE_PRESETS.get(preset, {}).get('label', root.name),
    }


def _sidecar_files(path: Path) -> list[Path]:
    extras: list[Path] = []
    suffix = path.suffix.lower()
    if suffix == '.onnx':
        for candidate in (path.with_suffix('.onnx.json'), Path(f'{path}.json')):
            if candidate.is_file():
                extras.append(candidate.resolve())
    if suffix == '.gguf':
        try:
            for sibling in path.parent.glob('*.gguf'):
                if 'mmproj' in sibling.name.lower():
                    extras.append(sibling.resolve())
        except OSError:
            pass
    return extras


def collect_model_files(path: str | Path, preset: str = 'custom') -> list[Path]:
    root = Path(str(path)).expanduser().resolve()
    if not root.is_dir():
        return []
    preset_key = str(preset or 'custom').strip().lower()
    detectors = _PRESET_DETECTORS.get(preset_key, _PRESET_DETECTORS['custom'])
    seen: set[str] = set()
    files: list[Path] = []
    for name in detectors:
        fn = _DETECTORS[name]
        result = fn(root)
        for raw in result.get('files') or []:
            try:
                resolved = Path(raw).resolve()
            except OSError:
                continue
            key = str(resolved).lower()
            if key in seen or not resolved.is_file():
                continue
            seen.add(key)
            files.append(resolved)
    for path_item in list(files):
        for sidecar in _sidecar_files(path_item):
            key = str(sidecar).lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(sidecar)
    return files


def _analyze_directory(path: Path, preset: str) -> dict[str, Any] | None:
    stats = summarize_library_path(path, preset)
    if not stats['exists'] or stats['model_count'] <= 0:
        return None
    model_type = stats['model_type']
    count = stats['model_count']
    samples = stats['sample_models']
    return {
        'path': str(path.resolve()),
        'preset': preset if preset in _STORAGE_PRESETS else 'custom',
        'label': _label_for(preset, path, model_type, count, samples),
        'model_type': model_type,
        'model_count': count,
        'sample_models': samples,
        'size_gb': stats['size_gb'],
        'exists': True,
    }


def scan_for_preset(
    preset: str = 'custom',
    *,
    query: str = '',
    extra_roots: list[str] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preset_key = str(preset or 'custom').strip().lower()
    if preset_key not in _PRESET_DETECTORS:
        preset_key = 'custom'
    config = cfg or load_config()
    started = time.time()
    roots = seed_scan_roots(config)
    if preset_key not in ('custom', 'dflash'):
        roots.insert(0, _preset_path(preset_key, config))
    for raw in extra_roots or []:
        path = Path(str(raw)).expanduser()
        if path not in roots:
            roots.insert(0, path)
    needle = str(query or '').strip().lower()
    candidates: dict[str, dict[str, Any]] = {}
    dirs_seen = 0

    stack: list[tuple[Path, int]] = [(root, 0) for root in roots if root.exists()]
    while stack and dirs_seen < _MAX_DIRS and (time.time() - started) < _MAX_SCAN_SECONDS:
        current, depth = stack.pop()
        dirs_seen += 1
        if needle and needle not in current.as_posix().lower():
            pass
        hit = _analyze_directory(current, preset_key)
        if hit:
            key = _norm(Path(hit['path']))
            prev = candidates.get(key)
            if not prev or hit['model_count'] > prev['model_count']:
                candidates[key] = hit
        if depth >= _MAX_DEPTH:
            continue
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            if _should_skip_dir(entry.name):
                continue
            stack.append((entry, depth + 1))

    known = {_preset_path('lmstudio', config), _preset_path('dflash', config)}
    for path in known:
        if not path.is_dir():
            continue
        if preset_key == 'lmstudio' and 'lmstudio' not in _norm(path):
            continue
        hit = _analyze_directory(path, preset_key)
        if hit:
            candidates[_norm(path)] = hit

    rows = sorted(candidates.values(), key=lambda row: (-int(row.get('model_count') or 0), row.get('label') or ''))
    if needle:
        rows = [row for row in rows if needle in str(row.get('path') or '').lower() or needle in str(row.get('label') or '').lower()]
    return {
        'success': True,
        'preset': preset_key,
        'candidates': rows[:40],
        'scanned_dirs': dirs_seen,
        'elapsed_ms': int((time.time() - started) * 1000),
    }
