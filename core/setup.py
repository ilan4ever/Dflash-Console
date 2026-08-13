"""First-run setup helpers — scan, library suggestions, completion state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import CONFIG_PATH, get_dflash_root, load_config
from core.model_discovery import scan_for_preset
from core.model_paths import _preset_path, get_models_root, normalize_model_libraries


def is_setup_complete(cfg: dict[str, Any] | None = None) -> bool:
    config = cfg or load_config()
    if config.get('setup_complete') is True:
        return True
    if config.get('setup_complete') is False:
        return False
    libs = config.get('model_libraries')
    if isinstance(libs, list) and libs:
        return True
    # Older Console configurations predate model_libraries. A configured
    # server or explicit models_root proves that setup already happened; do
    # not interrupt those users with the first-run wizard after an upgrade.
    servers = config.get('servers')
    if isinstance(servers, list) and any(isinstance(row, dict) for row in servers):
        return True
    if str(config.get('models_root') or '').strip():
        return True
    return False


def auto_approve_library(candidate: dict[str, Any]) -> bool:
    count = int(candidate.get('model_count') or 0)
    if count <= 0:
        return False
    preset = str(candidate.get('preset') or '').strip().lower()
    path = str(candidate.get('path') or '').replace('\\', '/').lower()
    if preset in {'dflash', 'lmstudio', 'gguf', 'speech', 'tts', 'ocr', 'embeddings'}:
        return True
    if '/.lmstudio/' in path:
        return True
    if '/.cache/huggingface' in path:
        return True
    if '/models/' in path or path.endswith('/models'):
        return True
    return count > 0


def _norm_path_key(path: str | Path) -> str:
    try:
        return str(Path(str(path)).expanduser().resolve()).lower()
    except OSError:
        return str(path).lower()


def _merge_candidate(
    store: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> None:
    key = _norm_path_key(row.get('path') or '')
    if not key:
        return
    prev = store.get(key)
    if not prev or int(row.get('model_count') or 0) > int(prev.get('model_count') or 0):
        store[key] = row


def scan_setup_candidates(*, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = cfg or load_config()
    merged: dict[str, dict[str, Any]] = {}

    for preset in ('custom', 'dflash', 'lmstudio', 'gguf', 'speech', 'tts', 'ocr', 'embeddings'):
        payload = scan_for_preset(preset, cfg=config)
        for row in payload.get('candidates') or []:
            if not isinstance(row, dict):
                continue
            _merge_candidate(merged, row)

    models_root = get_models_root(config)
    if models_root.is_dir():
        hit = scan_for_preset('dflash', cfg=config)
        for row in hit.get('candidates') or []:
            if _norm_path_key(row.get('path')) == _norm_path_key(models_root):
                _merge_candidate(merged, row)
                break
        else:
            from core.model_discovery import summarize_library_path

            stats = summarize_library_path(models_root, 'dflash')
            if int(stats.get('model_count') or 0) > 0 or models_root.is_dir():
                _merge_candidate(merged, {
                    'path': str(models_root),
                    'preset': 'dflash',
                    'label': 'DFlash Console models',
                    'model_type': stats.get('model_type') or 'gguf',
                    'model_count': stats.get('model_count') or 0,
                    'sample_models': stats.get('sample_models') or [],
                    'size_gb': stats.get('size_gb'),
                    'exists': True,
                })

    for preset in ('lmstudio', 'speech', 'tts', 'ocr', 'embeddings'):
        known = _preset_path(preset, config)
        if not known.is_dir():
            continue
        from core.model_discovery import summarize_library_path

        stats = summarize_library_path(known, preset)
        count = int(stats.get('model_count') or 0)
        if count <= 0 and preset != 'dflash':
            continue
        _merge_candidate(merged, {
            'path': str(known.resolve()),
            'preset': preset,
            'label': stats.get('label_hint') or known.name,
            'model_type': stats.get('model_type') or 'unknown',
            'model_count': count,
            'sample_models': stats.get('sample_models') or [],
            'size_gb': stats.get('size_gb'),
            'exists': True,
        })

    rows = sorted(
        merged.values(),
        key=lambda row: (
            0 if auto_approve_library(row) else 1,
            -int(row.get('model_count') or 0),
            str(row.get('label') or row.get('path') or ''),
        ),
    )
    for row in rows:
        row['suggested'] = auto_approve_library(row)
    return rows


def build_setup_scan_payload(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    candidates = scan_setup_candidates(cfg=config)
    total_models = sum(int(row.get('model_count') or 0) for row in candidates)
    suggested = sum(1 for row in candidates if row.get('suggested'))
    return {
        'success': True,
        'setup_complete': is_setup_complete(config),
        'data_root': str(CONFIG_PATH.parent),
        'dflash_root': str(get_dflash_root(config)),
        'models_root': str(get_models_root(config)),
        'candidates': candidates,
        'summary': {
            'folder_count': len(candidates),
            'model_count': total_models,
            'suggested_count': suggested,
        },
    }


def candidate_to_library(row: dict[str, Any], *, index: int) -> dict[str, Any]:
    preset = str(row.get('preset') or 'custom').strip().lower()
    label = str(row.get('label') or row.get('path') or f'Library {index + 1}').strip()
    lib_id = f"{preset}-{_norm_path_key(row.get('path') or label)[:32]}-{index}"
    return {
        'id': lib_id.replace(':', '-'),
        'label': label,
        'path': str(row.get('path') or ''),
        'enabled': True,
        'preset': preset if preset in {
            'dflash', 'gguf', 'lmstudio', 'speech', 'tts', 'ocr', 'embeddings', 'custom',
        } else 'custom',
        'download_default': index == 0,
    }


def complete_setup(
    selected: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(cfg or load_config())
    libraries = normalize_model_libraries(selected, cfg=config)
    if not libraries:
        libraries = normalize_model_libraries([], cfg=config)
    config['model_libraries'] = libraries
    config['setup_complete'] = True
    return config
