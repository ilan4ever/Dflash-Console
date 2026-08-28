"""Decide which running engines must restart after a hardware settings change."""

from __future__ import annotations

from typing import Any

from core.config import is_embedding_server, list_servers, normalize_hardware_settings, normalize_server
from core.gpu_devices import query_gpu_devices, resolve_role_gpu_launch_params
from core.server_boot import get_started_launch, _launch_signature

GPU_LAUNCH_KEYS = ('main_gpu', 'split_mode', 'tensor_split', 'offload_kv_cache_to_gpu')


def _fingerprint(signature: dict[str, Any] | None) -> tuple[Any, ...]:
    sig = signature or {}
    return (
        int(sig.get('main_gpu') or 0),
        str(sig.get('split_mode') or 'none'),
        str(sig.get('tensor_split') or ''),
        bool(sig.get('offload_kv_cache_to_gpu', True)),
    )


def _desired_signature(
    entry: dict[str, Any],
    *,
    cfg: dict[str, Any],
    gpus: list[dict[str, Any]],
    model_id: str,
) -> dict[str, Any]:
    launch = resolve_role_gpu_launch_params(
        entry.get('gpu_device'),
        model_id=model_id,
        gpus=gpus,
        hardware=cfg.get('hardware_settings'),
        context_size=entry.get('context_size'),
    )
    return _launch_signature(entry, launch, cfg=cfg)


def hardware_reload_plan(
    cfg: dict[str, Any],
    *,
    gpus: list[dict[str, Any]] | None = None,
    force_reload: bool = False,
) -> dict[str, Any]:
    """Return loaded Console engines that must apply the current GPU settings."""
    from core.runtime import probe_models, tcp_port_open

    config = dict(cfg or {})
    config['hardware_settings'] = normalize_hardware_settings(config.get('hardware_settings'))
    devices = list(gpus) if gpus is not None else query_gpu_devices()
    targets: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []

    for raw in list_servers(config):
        entry = normalize_server(raw)
        if entry.get('enabled', True) is False:
            continue
        port = int(entry.get('port') or 0)
        host = str(entry.get('host') or '127.0.0.1')
        api_url = str(entry.get('api_url') or '')
        server_id = str(entry.get('id') or '')
        if port <= 0 or not server_id or not tcp_port_open(host, port):
            continue
        models = probe_models(api_url) if api_url else []
        if not models:
            continue
        model_id = str(entry.get('model_id') or models[0] or '').strip()
        desired = _desired_signature(entry, cfg=config, gpus=devices, model_id=model_id)
        running = get_started_launch(port)
        row = {
            'server_id': server_id,
            'label': str(entry.get('label') or server_id),
            'models': models,
            'gpu_device': str(entry.get('gpu_device') or 'auto'),
            'embedding': is_embedding_server(entry),
            'from': {key: running.get(key) for key in GPU_LAUNCH_KEYS} if running else {},
            'to': {key: desired.get(key) for key in GPU_LAUNCH_KEYS},
        }
        if not force_reload and running and _fingerprint(running) == _fingerprint(desired):
            unchanged.append(row)
            continue
        targets.append(row)

    if targets:
        names = ', '.join(item['label'] for item in targets)
        message = (
            'Loaded models keep the old GPU layout until they are started again. '
            f'Reloading now: {names}.'
        )
    elif unchanged:
        message = 'Compute settings saved. Loaded models already match this layout.'
    else:
        message = 'Compute settings saved. The next model you load will use this layout.'

    return {
        'reload_needed': bool(targets),
        'reload_targets': targets,
        'reload_unchanged': unchanged,
        'reload_message': message,
    }
