"""Aggregated status payloads for external integrations."""

from __future__ import annotations

import time
from typing import Any

from core.config import list_runtimes, list_servers, load_config, normalize_server
from core.gpu_devices import get_gpu_devices_payload
from core.runtime import get_status_payload
from core.runtimes import get_runtime_adapter, runtime_ids
from core.system_stats import get_system_stats_payload


def _runtime_rows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for runtime in list_runtimes(cfg):
        runtime_id = str(runtime.get('runtime_id') or '')
        adapter = get_runtime_adapter(runtime_id)
        health = adapter.health() if adapter is not None and callable(getattr(adapter, 'health', None)) else {}
        attribution: dict[str, Any] = {
            'pids': [],
            'vram_by_gpu': {},
            'vram_total_gb': None,
            'vram_source': 'unavailable',
        }
        if health.get('running') is True and int(health.get('port') or 0) > 0:
            try:
                from core.gpu_processes import process_attribution_for_port

                attribution = process_attribution_for_port(
                    int(health.get('port') or 0),
                    str(health.get('host') or '127.0.0.1'),
                )
            except Exception:
                pass
        component_key = f'onevoice.{runtime_id}' if runtime_id else ''
        component_label = {
            'vllm': 'OneVoice vLLM',
            'transformers': 'OneVoice Transformers',
            'freetoken': 'OneVoice FreeToken (WSL)',
            'faster-whisper': 'OneVoice Faster-Whisper STT',
            'vibevoice': 'OneVoice VibeVoice TTS',
            'stt': 'OneVoice STT',
        }.get(runtime_id, str(runtime.get('label') or runtime_id))
        active_model = str(health.get('active_model') or '').strip()
        model_id = active_model.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1] if active_model else ''
        rows.append({
            'id': str(runtime.get('id') or ''),
            'runtime_id': runtime_id,
            'label': str(runtime.get('label') or runtime.get('id') or ''),
            'port': int(health.get('port') or runtime.get('port') or 0),
            'api_url': str(health.get('api_url') or runtime.get('api_url') or ''),
            'enabled': runtime.get('enabled', True) is not False,
            'running': health.get('running') is True,
            'active_model': active_model,
            'active_model_id': model_id,
            'active_device': health.get('device') or health.get('active_device') or '',
            'component_key': component_key,
            'component_label': component_label,
            'component_role': runtime_id,
            'adapter_installed': adapter is not None,
            **attribution,
        })
    return rows


def _loaded_from_engines(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        models = [str(model_id) for model_id in (server.get('loaded_models') or []) if str(model_id).strip()]
        if not models:
            continue
        cards = list(server.get('visible_cards') or [])
        primary_card = cards[0] if cards else {}
        loaded.append({
            'kind': 'engine',
            'server_id': str(server.get('id') or ''),
            'label': str(server.get('label') or server.get('id') or ''),
            'status': str(server.get('status') or ''),
            'runtime_id': 'llama-server',
            'api_url': str(server.get('api_url') or ''),
            'loaded_models': models,
            'active_model_id': str(server.get('active_model_id') or models[0]),
            'model_path': str(primary_card.get('path') or server.get('model_path') or ''),
            'ready_for_chat': bool(server.get('ready_for_chat')),
            'ready_for_embedding': bool(server.get('ready_for_embedding')),
            'inference_stats': server.get('inference_stats') or {},
            'gpu_display': server.get('gpu_display') or '',
            'component_key': server.get('component_key') or '',
            'component_label': server.get('component_label') or '',
            'component_role': server.get('component_role') or '',
            'pids': server.get('pids') or [],
            'vram_by_gpu': server.get('vram_by_gpu') or {},
            'vram_total_gb': server.get('vram_total_gb'),
            'vram_source': server.get('vram_source') or '',
        })
    return loaded


def _loaded_from_runtimes(runtimes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for runtime in runtimes:
        active = str(runtime.get('active_model') or '').strip()
        if not active:
            continue
        folder = active.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
        runtime_id = str(runtime.get('runtime_id') or '')
        loaded.append({
            'kind': 'runtime',
            'runtime_id': runtime_id,
            'id': str(runtime.get('id') or ''),
            'server_id': runtime_id,
            'model_id': folder.lower(),
            'label': folder or str(runtime.get('label') or runtime.get('id') or ''),
            'status': 'loaded' if runtime.get('running') else 'ready',
            'active_model': active,
            'model_path': active,
            'api_url': str(runtime.get('api_url') or ''),
            'active_device': str(runtime.get('active_device') or ''),
            'component_key': runtime.get('component_key') or f"onevoice.{runtime_id}",
            'component_label': runtime.get('component_label') or runtime.get('label') or runtime_id,
            'component_role': runtime.get('component_role') or runtime_id,
            'pids': runtime.get('pids') or [],
            'vram_by_gpu': runtime.get('vram_by_gpu') or {},
            'vram_total_gb': runtime.get('vram_total_gb'),
            'vram_source': runtime.get('vram_source') or '',
        })
    return loaded


def get_loaded_models_payload(*, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return currently loaded models across engines and non-llama runtimes."""
    config = cfg or load_config()
    enabled = [s for s in list_servers(config) if s.get('enabled', True)]
    gpus = get_gpu_devices_payload().get('gpus') or []
    engines = get_status_payload(enabled, cfg=config, gpus=gpus, include_external=False)
    server_rows = [row for row in (engines.get('servers') or []) if isinstance(row, dict)]
    runtime_rows = _runtime_rows(config)
    engine_loaded = _loaded_from_engines(server_rows)
    runtime_loaded = _loaded_from_runtimes(runtime_rows)
    return {
        'success': True,
        'updated_at': time.time(),
        'count': len(engine_loaded) + len(runtime_loaded),
        'engines': engine_loaded,
        'runtimes': runtime_loaded,
        'loaded': engine_loaded + runtime_loaded,
    }


def get_status_report_payload(*, cfg: dict[str, Any] | None = None, include_external: bool = True) -> dict[str, Any]:
    """Full machine report: system monitoring, GPUs, engines, and loaded models."""
    config = cfg or load_config()
    system = get_system_stats_payload()
    gpu_devices = get_gpu_devices_payload()
    enabled = [s for s in list_servers(config) if s.get('enabled', True)]
    gpus = gpu_devices.get('gpus') or system.get('gpus') or []
    engines = get_status_payload(
        enabled,
        cfg=config,
        gpus=gpus,
        include_external=include_external,
    )
    runtime_rows = _runtime_rows(config)
    loaded_payload = get_loaded_models_payload(cfg=config)
    from core.remote_gpu import with_shared_gpus

    shared_devices = dict(gpu_devices)
    shared_list = with_shared_gpus(gpu_devices.get('gpus') or [])
    shared_devices['gpus'] = shared_list
    shared_devices['count'] = len(shared_list)
    system = dict(system)
    system['gpus'] = with_shared_gpus(system.get('gpus') or [])
    return {
        'success': True,
        'updated_at': time.time(),
        'system': system,
        'gpu_devices': shared_devices,
        'engines': {
            'success': True,
            'servers': engines.get('servers') or [],
            'primary_server_id': engines.get('primary_server_id') or '',
            'all_servers': [normalize_server(s) for s in list_servers(config)],
            'external_gpu_loads': engines.get('external_gpu_loads') or [],
            'stale': bool(engines.get('stale')),
            'stale_age_ms': engines.get('stale_age_ms'),
        },
        'runtimes': runtime_rows,
        'runtime_adapters': sorted(runtime_ids()),
        'loaded': loaded_payload,
        'gateway_hint': 'GET /api/gateway for the OpenAI-compatible proxy URL',
    }
