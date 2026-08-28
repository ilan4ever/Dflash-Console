"""GPU contention scaffolding for the multi-modal runtime.

Phase 0: expose which Console runtimes hold VRAM and which external processes
(Ollama, LM Studio, ...) are consuming GPU memory, so the load-intent UX can
offer "stop other runtimes" for Console-owned processes and warn by name for
external ones. The Console never claims it can safely kill external apps.

This module deliberately avoids importing core.runtime / core.server_boot at
module import time so the runtimes package stays import-free of the lifecycle
layer (prevents circular imports).
"""

from __future__ import annotations

from typing import Any

from core.config import list_servers, load_config, normalize_server
from core.runtime import _cached_external_gpu_loads, probe_runtime_state, tcp_port_open


def gpu_contention_report(
    *,
    cfg: dict[str, Any] | None = None,
    include_external: bool = True,
) -> dict[str, Any]:
    """Describe current GPU holders: Console servers + external processes.

    ``recommendation`` is one of:
      - ``stop-others``   at least one Console runtime is holding VRAM
      - ``warn-external`` only external processes are holding VRAM
      - ``none``          no significant GPU compute detected

    External GPU rows come from the shared status-payload cache (populated by
    the normal ``/api/servers`` poll) so this endpoint stays fast and never
    blocks on a fresh nvidia-smi / LM Studio / Ollama scan.
    """
    config = cfg or load_config()
    servers = [
        normalize_server(s)
        for s in list_servers(config)
        if s.get('enabled', True)
    ]

    console_running: list[dict[str, Any]] = []
    for server in servers:
        port = int(server.get('port') or 0)
        host = str(server.get('host') or '127.0.0.1')
        api_url = str(server.get('api_url') or '')
        running = bool(port and tcp_port_open(host, port))
        loaded: list[str] = []
        if running and api_url:
            try:
                loaded_ids, _loading, _router, _progress = probe_runtime_state(api_url)
                loaded = list(loaded_ids)
            except Exception:
                loaded = []
        console_running.append({
            'id': str(server.get('id') or ''),
            'runtime_id': 'llama-server',
            'label': str(server.get('label') or server.get('id') or ''),
            'port': port,
            'running': running,
            'loaded_models': loaded,
            'vram_estimate_mb': None,
        })

    from core.runtimes import get_runtime_adapter

    for runtime_id, label in (('vllm', 'vLLM'), ('transformers', 'Transformers')):
        adapter = get_runtime_adapter(runtime_id)
        if adapter is None or not callable(getattr(adapter, 'health', None)):
            continue
        health = adapter.health()
        if health.get('running') is not True:
            continue
        model = str(health.get('active_model') or '')
        console_running.append({
            'id': runtime_id,
            'runtime_id': runtime_id,
            'label': label,
            'port': int(health.get('port') or 0),
            'running': True,
            'loaded_models': [model] if model else [],
            'vram_estimate_mb': None,
        })

    external: list[dict[str, Any]] = []
    external_scan_pending = False
    if include_external:
        try:
            cached = _cached_external_gpu_loads()
        except Exception:
            cached = []
        for row in cached:
            external.append({
                'id': str(row.get('id') or ''),
                'title': str(row.get('title') or row.get('app') or ''),
                'app': str(row.get('app') or ''),
                'vram_mb': row.get('vram_mb'),
                'pid': row.get('pid'),
                'model_kind': str(row.get('model_kind') or ''),
            })
        if not cached:
            # First status poll may not have run yet; do not block on a fresh scan.
            external_scan_pending = True

    console_holding_vram = [row for row in console_running if row.get('running')]
    if console_holding_vram:
        recommendation = 'stop-others'
    elif external:
        recommendation = 'warn-external'
    else:
        recommendation = 'none'

    return {
        'success': True,
        'recommendation': recommendation,
        'console_runtimes': console_running,
        'external': external,
        'external_scan_pending': external_scan_pending,
    }
