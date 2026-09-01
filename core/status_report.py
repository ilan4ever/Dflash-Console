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
        rows.append({
            'id': str(runtime.get('id') or ''),
            'runtime_id': runtime_id,
            'label': str(runtime.get('label') or runtime.get('id') or ''),
            'port': int(health.get('port') or runtime.get('port') or 0),
            'api_url': str(health.get('api_url') or runtime.get('api_url') or ''),
            'enabled': runtime.get('enabled', True) is not False,
            'running': health.get('running') is True,
            'active_model': health.get('active_model') or '',
            'active_device': health.get('device') or health.get('active_device') or '',
            'adapter_installed': adapter is not None,
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
        })
    return loaded


def _loaded_from_runtimes(runtimes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for runtime in runtimes:
        active = str(runtime.get('active_model') or '').strip()
        if not active:
            continue
        loaded.append({
            'kind': 'runtime',
            'runtime_id': str(runtime.get('runtime_id') or ''),
            'id': str(runtime.get('id') or ''),
            'label': str(runtime.get('label') or runtime.get('id') or ''),
            'status': 'loaded' if runtime.get('running') else 'ready',
            'active_model': active,
            'model_path': active,
            'api_url': str(runtime.get('api_url') or ''),
            'active_device': str(runtime.get('active_device') or ''),
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
    return {
        'success': True,
        'updated_at': time.time(),
        'system': system,
        'gpu_devices': gpu_devices,
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
