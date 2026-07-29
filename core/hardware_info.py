"""Hardware discovery and persisted hardware preferences."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import CONFIG_PATH, get_dflash_root, load_config, normalize_hardware_settings
from core.gpu_devices import query_gpu_devices
from core.local_models import list_local_models
from core.system_stats import get_cpu_info_payload, get_system_stats_payload


def get_hardware_payload(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    stats = get_system_stats_payload()
    static_gpus = query_gpu_devices()
    live_by_index = {
        int(item.get('index')): item
        for item in (stats.get('gpus') or [])
        if isinstance(item, dict) and item.get('index') is not None
    }
    merged_gpus: list[dict[str, Any]] = []
    for gpu in static_gpus:
        live = live_by_index.get(int(gpu['index']), {})
        merged_gpus.append({
            **gpu,
            'load_percent': live.get('load_percent', 0),
            'vram_percent': live.get('vram_percent', 0),
            'vram_used_gb': live.get('vram_used_gb'),
            'vram_total_gb': live.get('vram_total_gb') or gpu.get('vram_gb'),
        })

    from core.model_paths import get_download_library, get_model_libraries, storage_presets

    models_meta = list_local_models(cfg=config)
    libraries = get_model_libraries(config)
    from core.model_discovery import summarize_library_path
    enriched_libraries = []
    for row in libraries:
        stats = summarize_library_path(row.get('path') or '', str(row.get('preset') or 'custom'))
        enriched_libraries.append({**row, **stats})
    studio_root = Path(__file__).resolve().parent.parent
    total_vram = round(
        sum(float(item.get('vram_total_gb') or item.get('vram_gb') or 0) for item in merged_gpus),
        2,
    )
    return {
        'success': True,
        'hardware_settings': normalize_hardware_settings(config.get('hardware_settings')),
        'cpu': get_cpu_info_payload(),
        'ram': {
            'used_gb': stats.get('ram_used_gb'),
            'total_gb': stats.get('ram_total_gb'),
            'percent': stats.get('ram_percent'),
        },
        'vram_total_gb': total_vram,
        'gpus': merged_gpus,
        'gpu_count': len(merged_gpus),
        'models_dir': models_meta.get('models_dir'),
        'model_libraries': enriched_libraries,
        'download_library_id': get_download_library(config).get('id'),
        'storage_presets': storage_presets(),
        'dflash_root': str(get_dflash_root(config)),
        'config_path': str(CONFIG_PATH),
        'logs_dir': str(studio_root / 'logs'),
        'presets_dir': str(studio_root / 'logs' / 'presets'),
        'ui_port': int(config.get('ui_port') or 8900),
    }
