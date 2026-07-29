"""Load and persist DFlash Console configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / 'config.json'

VALID_PROFILES = frozenset({
    'gemma-chat',
    'gemma-ar',
    'gemma-12-ar',
    'qwen-dflash',
    'qwen-ar',
    'bonsai',
    'bonsai-spec',
})

DEFAULT_LOAD_SETTINGS: dict[str, Any] = {
    'gpu_layers': 99,
    'cpu_threads': 9,
    'eval_batch_size': 2048,
    'physical_batch_size': 512,
    'flash_attention': True,
}

DEFAULT_INFERENCE_SETTINGS: dict[str, Any] = {
    'temperature': 0.7,
    'top_p': 0.9,
    'top_k': 40,
    'repeat_penalty': 1.1,
}

DEFAULT_HARDWARE_SETTINGS: dict[str, Any] = {
    'gpu_strategy': 'split_evenly',
    'enabled_gpu_indices': [],
    'limit_offload_dedicated_vram': True,
    'offload_kv_cache_to_gpu': True,
}

SPECULATIVE_PROFILES = frozenset({'gemma-chat', 'qwen-dflash', 'bonsai-spec'})


def normalize_load_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_LOAD_SETTINGS)
    flash = raw.get('flash_attention')
    return {
        'gpu_layers': max(0, min(999, int(raw.get('gpu_layers') or DEFAULT_LOAD_SETTINGS['gpu_layers']))),
        'cpu_threads': max(1, min(64, int(raw.get('cpu_threads') or DEFAULT_LOAD_SETTINGS['cpu_threads']))),
        'eval_batch_size': max(32, min(8192, int(raw.get('eval_batch_size') or DEFAULT_LOAD_SETTINGS['eval_batch_size']))),
        'physical_batch_size': max(32, min(8192, int(raw.get('physical_batch_size') or DEFAULT_LOAD_SETTINGS['physical_batch_size']))),
        'flash_attention': flash is not False,
    }


def normalize_inference_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_INFERENCE_SETTINGS)
    return {
        'temperature': max(0.0, min(2.0, float(raw.get('temperature') if raw.get('temperature') is not None else DEFAULT_INFERENCE_SETTINGS['temperature']))),
        'top_p': max(0.0, min(1.0, float(raw.get('top_p') if raw.get('top_p') is not None else DEFAULT_INFERENCE_SETTINGS['top_p']))),
        'top_k': max(0, min(200, int(raw.get('top_k') if raw.get('top_k') is not None else DEFAULT_INFERENCE_SETTINGS['top_k']))),
        'repeat_penalty': max(1.0, min(2.0, float(raw.get('repeat_penalty') if raw.get('repeat_penalty') is not None else DEFAULT_INFERENCE_SETTINGS['repeat_penalty']))),
    }


def normalize_hardware_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    strategy = str(raw.get('gpu_strategy') or DEFAULT_HARDWARE_SETTINGS['gpu_strategy']).strip().lower()
    if strategy not in ('single_largest', 'split_evenly', 'split_by_vram'):
        strategy = DEFAULT_HARDWARE_SETTINGS['gpu_strategy']
    enabled_raw = raw.get('enabled_gpu_indices')
    enabled_indices: list[int] = []
    if isinstance(enabled_raw, list):
        for item in enabled_raw:
            try:
                enabled_indices.append(int(item))
            except (TypeError, ValueError):
                continue
    return {
        'gpu_strategy': strategy,
        'enabled_gpu_indices': enabled_indices,
        'limit_offload_dedicated_vram': raw.get('limit_offload_dedicated_vram') is not False,
        'offload_kv_cache_to_gpu': raw.get('offload_kv_cache_to_gpu') is not False,
    }


def get_dflash_root(cfg: dict[str, Any] | None = None) -> Path:
    raw = os.environ.get('DFLASH_ROOT') or (cfg or load_config()).get('dflash_root') or r'C:\dev\Dflash'
    return Path(str(raw)).resolve()


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {'ui_port': 8900, 'dflash_root': r'C:\dev\Dflash', 'servers': []}
    with CONFIG_PATH.open(encoding='utf-8') as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError('config.json must be a JSON object')
    servers = data.get('servers')
    if not isinstance(servers, list):
        data['servers'] = []
    data['hardware_settings'] = normalize_hardware_settings(data.get('hardware_settings'))
    return data


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')


def get_server(cfg: dict[str, Any], server_id: str) -> dict[str, Any] | None:
    for entry in cfg.get('servers') or []:
        if isinstance(entry, dict) and str(entry.get('id') or '') == server_id:
            return entry
    return None


def normalize_server(entry: dict[str, Any]) -> dict[str, Any]:
    port = int(entry.get('port') or 0)
    host = str(entry.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    api_url = str(entry.get('api_url') or f'http://{host}:{port}/v1').strip().rstrip('/')
    idle_minutes = entry.get('idle_unload_minutes')
    if idle_minutes is None:
        idle_seconds = int(entry.get('idle_unload_seconds') or 3600)
        idle_minutes = max(0, round(idle_seconds / 60))
    return {
        'id': str(entry.get('id') or '').strip(),
        'label': str(entry.get('label') or entry.get('id') or 'Server').strip(),
        'profile': str(entry.get('profile') or 'gemma-chat').strip(),
        'port': port,
        'host': host,
        'api_url': api_url,
        'model_id': str(entry.get('model_id') or '').strip(),
        'gpu_device': str(entry.get('gpu_device') or 'auto').strip().lower() or 'auto',
        'context_size': max(2048, int(entry.get('context_size') or 8192)),
        'idle_unload_minutes': max(0, int(idle_minutes or 0)),
        'enabled': entry.get('enabled', True) is not False,
        'load_settings': normalize_load_settings(entry.get('load_settings')),
        'inference_settings': normalize_inference_settings(entry.get('inference_settings')),
    }


def list_servers(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = cfg or load_config()
    result: list[dict[str, Any]] = []
    for entry in data.get('servers') or []:
        if not isinstance(entry, dict):
            continue
        normalized = normalize_server(entry)
        if normalized['id']:
            result.append(normalized)
    return result


def normalize_model_libraries(raw: Any, *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from core.model_paths import normalize_model_libraries as _normalize

    return _normalize(raw, cfg=cfg or load_config())
