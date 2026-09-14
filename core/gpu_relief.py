"""GPU saturation relief for Console-owned engines.

When a generation appears stuck on a saturated GPU (high utilization / VRAM),
unload idle Console models on the same GPU to free headroom. If another enabled
GPU has room, queue a reload there after the active generation finishes.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from core.config import (
    is_embedding_server,
    list_servers,
    load_config,
    normalize_hardware_settings,
    normalize_server,
    save_config,
)
from core.gpu_policy import vram_headroom_gb
from core.memory_guardrails import count_loaded_console_engines

logger = logging.getLogger('uvicorn.error')

TICK_INTERVAL_SECONDS = 12.0
MIN_GENERATING_SECONDS = 75.0
PREFILL_STALL_SECONDS = 30.0
DECODE_STALL_SECONDS = 30.0
GPU_LOAD_THRESHOLD = 90
VRAM_PERCENT_THRESHOLD = 88
RELIEF_COOLDOWN_SECONDS = 180.0
MAX_ACTIONS_PER_TICK = 1

_LOCK = threading.Lock()
_STALL_TRACK: dict[str, dict[str, Any]] = {}
_LAST_RELIEF_AT: dict[str, float] = {}
_PENDING_RELOCATE: list[dict[str, Any]] = []
_WATCHDOG_STARTED = False


def gpu_relief_enabled(cfg: dict[str, Any] | None = None) -> bool:
    config = cfg or load_config()
    hw = normalize_hardware_settings(config.get('hardware_settings'))
    return hw.get('gpu_relief_enabled') is not False


def _gpu_by_index(gpus: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in gpus:
        try:
            out[int(row.get('index'))] = row
        except (TypeError, ValueError):
            continue
    return out


def gpu_is_saturated(gpu_row: dict[str, Any] | None) -> bool:
    if not gpu_row:
        return False
    try:
        load_percent = int(gpu_row.get('load_percent') or 0)
        vram_percent = int(gpu_row.get('vram_percent') or 0)
    except (TypeError, ValueError):
        return False
    return load_percent >= GPU_LOAD_THRESHOLD or vram_percent >= VRAM_PERCENT_THRESHOLD


def detect_generation_stall(
    stats: dict[str, Any],
    track: dict[str, Any],
    *,
    now: float,
    gpu_saturated: bool,
) -> bool:
    """Return True when an active generation looks stuck on a saturated GPU."""
    if not stats.get('generating'):
        track.clear()
        return False

    generating_seconds = float(stats.get('generating_seconds') or 0.0)
    if generating_seconds < MIN_GENERATING_SECONDS:
        return False

    if not gpu_saturated:
        track['stall_since'] = None
        return False

    generating_tokens = stats.get('generating_tokens')
    if generating_tokens is not None and int(generating_tokens) > 0:
        last_decode = track.get('last_decode_tokens')
        if last_decode is not None and int(generating_tokens) <= int(last_decode):
            stalled_for = now - float(track.get('last_decode_at') or now)
            if stalled_for >= DECODE_STALL_SECONDS:
                return True
        else:
            track['last_decode_tokens'] = int(generating_tokens)
            track['last_decode_at'] = now
            track['stall_since'] = None
        return False

    prefill_tokens = stats.get('prefill_tokens')
    if prefill_tokens is not None:
        last_prefill = track.get('last_prefill')
        if last_prefill is not None and int(prefill_tokens) > int(last_prefill):
            track['last_prefill'] = int(prefill_tokens)
            track['last_prefill_at'] = now
            track['stall_since'] = None
            return False
        track['last_prefill'] = int(prefill_tokens)
        if track.get('last_prefill_at') is None:
            track['last_prefill_at'] = now

    prefill_age = now - float(track.get('last_prefill_at') or now)
    if prefill_tokens is not None and prefill_age >= PREFILL_STALL_SECONDS:
        if track.get('stall_since') is None:
            track['stall_since'] = now
        if now - float(track['stall_since']) >= PREFILL_STALL_SECONDS:
            return True
        return False

    if track.get('stall_since') is None:
        track['stall_since'] = now
    return (now - float(track['stall_since'])) >= PREFILL_STALL_SECONDS


def _pick_alternate_gpu(
    cfg: dict[str, Any],
    *,
    exclude_index: int,
    required_gb: float,
    gpus: list[dict[str, Any]],
) -> int | None:
    hw = normalize_hardware_settings(cfg.get('hardware_settings'))
    enabled = {int(i) for i in (hw.get('enabled_gpu_indices') or [])}
    headroom = vram_headroom_gb(cfg)
    best: tuple[float, int] | None = None
    for row in gpus:
        try:
            index = int(row.get('index'))
        except (TypeError, ValueError):
            continue
        if index == exclude_index:
            continue
        if enabled and index not in enabled:
            continue
        free_gb = float(row.get('vram_total_gb') or 0.0) - float(row.get('vram_used_gb') or 0.0)
        if free_gb < required_gb + headroom:
            continue
        if best is None or free_gb > best[0]:
            best = (free_gb, index)
    return best[1] if best else None


def _set_server_gpu_device(cfg: dict[str, Any], server_id: str, gpu_index: int) -> bool:
    changed = False
    for server in cfg.get('servers') or []:
        if str(server.get('id') or '') != server_id:
            continue
        server['gpu_device'] = str(int(gpu_index))
        changed = True
        break
    if changed:
        save_config(cfg)
    return changed


def unload_console_engine_for_relief(server_id: str, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Unload a Console engine checkpoint without stopping its listener."""
    from core.client_identity import clear_active_clients
    from core.engine_state import note_engine_idle
    from core.load_progress import append_log
    from core.runtime import probe_models, tcp_port_open, unload_model
    from core.server_boot import note_boot_cycle_end, start_router_listener

    config = cfg or load_config()
    server = next(
        (
            normalize_server(row)
            for row in list_servers(config)
            if str(row.get('id') or '') == str(server_id or '').strip()
        ),
        None,
    )
    if not server:
        return {'success': False, 'error': 'server not found'}
    if is_embedding_server(server):
        return {'success': False, 'error': 'embedding engines cannot be relieved'}

    host = str(server.get('host') or '127.0.0.1')
    port = int(server.get('port') or 0)
    api_url = str(server.get('api_url') or '')
    if port <= 0 or not tcp_port_open(host, port) or not api_url:
        return {'success': False, 'error': 'listener not running'}

    loaded_ids = probe_models(api_url)
    model_id = str((loaded_ids[0] if loaded_ids else server.get('model_id')) or '').strip()
    if not model_id:
        note_engine_idle(server_id)
        return {'success': True, 'unloaded': False, 'message': 'no model loaded'}

    result = unload_model(api_url=api_url, model_id=model_id)
    if not result.get('success'):
        return result

    append_log(
        server_id,
        f"=== gpu relief unload {time.strftime('%Y-%m-%d %H:%M:%S')} model={model_id} ===",
    )
    note_boot_cycle_end(port)
    listener_ready = tcp_port_open(host, port)
    if not listener_ready:
        restart = start_router_listener(server, cfg=config)
        listener_ready = bool(restart.get('success')) and tcp_port_open(host, port)
    note_engine_idle(server_id)
    clear_active_clients(server_id)
    return {
        **result,
        'listener_ready': listener_ready,
        'relief': True,
    }


def _relief_candidates(
    cfg: dict[str, Any],
    *,
    stalled_server_id: str,
    stalled_gpu_index: int,
    generating_ids: set[str],
) -> list[dict[str, Any]]:
    from core.client_identity import list_active_clients
    from core.inference_stats import is_proxy_generating

    _loaded_count, rows = count_loaded_console_engines(cfg, exclude_server_id=stalled_server_id)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        server_id = str(row.get('id') or '')
        if not server_id or server_id in generating_ids:
            continue
        if is_proxy_generating(server_id):
            continue
        if list_active_clients(server_id):
            continue
        try:
            gpu_index = int(row.get('gpu_index'))
        except (TypeError, ValueError):
            continue
        if gpu_index != stalled_gpu_index:
            continue
        last_relief = _LAST_RELIEF_AT.get(server_id)
        if last_relief and (time.time() - last_relief) < RELIEF_COOLDOWN_SECONDS:
            continue
        candidates.append(row)
    candidates.sort(key=lambda item: float(item.get('estimated_gb') or 0.0))
    return candidates


def _queue_relocate_reload(
    *,
    server_id: str,
    target_gpu: int,
    wait_server_id: str,
    reason: str,
) -> None:
    with _LOCK:
        for item in _PENDING_RELOCATE:
            if str(item.get('server_id') or '') == server_id:
                return
        _PENDING_RELOCATE.append({
            'server_id': server_id,
            'target_gpu': int(target_gpu),
            'wait_server_id': wait_server_id,
            'reason': reason,
            'queued_at': time.time(),
        })


def _process_pending_relocates(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from core.inference_stats import is_proxy_generating
    from core.server_boot import load_server_checkpoint

    completed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    with _LOCK:
        pending = list(_PENDING_RELOCATE)
        _PENDING_RELOCATE.clear()

    for item in pending:
        wait_id = str(item.get('wait_server_id') or '')
        if wait_id and is_proxy_generating(wait_id):
            remaining.append(item)
            continue
        server_id = str(item.get('server_id') or '')
        server = next(
            (normalize_server(row) for row in list_servers(cfg) if str(row.get('id') or '') == server_id),
            None,
        )
        if not server:
            continue
        target_gpu = int(item.get('target_gpu') or 0)
        _set_server_gpu_device(cfg, server_id, target_gpu)
        server = normalize_server(server)
        server['gpu_device'] = str(target_gpu)
        result = load_server_checkpoint(server, cfg=cfg)
        completed.append({
            'server_id': server_id,
            'target_gpu': target_gpu,
            'success': bool(result.get('success')),
            'error': result.get('error'),
            'reason': item.get('reason'),
        })
        if result.get('success'):
            logger.info(
                'gpu relief relocated %s to GPU %s after %s finished',
                server_id,
                target_gpu,
                wait_id,
            )

    if remaining:
        with _LOCK:
            _PENDING_RELOCATE.extend(remaining)
    return completed


def tick_gpu_relief(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """One watchdog pass. Safe to call from tests."""
    if not gpu_relief_enabled(cfg):
        return {'success': True, 'enabled': False, 'actions': []}

    from core.inference_stats import fetch_inference_stats, is_proxy_generating
    from core.system_stats import get_system_stats_payload

    config = cfg or load_config()
    reloads = _process_pending_relocates(config)
    gpus = list(get_system_stats_payload().get('gpus') or [])
    gpu_map = _gpu_by_index(gpus)
    now = time.time()
    actions: list[dict[str, Any]] = []

    stalled_servers: list[tuple[str, int, dict[str, Any]]] = []
    generating_ids: set[str] = set()

    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        server_id = str(server.get('id') or '')
        if not server_id or is_embedding_server(server):
            continue
        if not is_proxy_generating(server_id):
            with _LOCK:
                _STALL_TRACK.pop(server_id, None)
            continue

        generating_ids.add(server_id)
        api_url = str(server.get('api_url') or '')
        stats = fetch_inference_stats(
            api_url,
            server_id=server_id,
            model_id=str(server.get('model_id') or ''),
            api_key=str(server.get('api_key') or ''),
        )
        _loaded_count, loaded_rows = count_loaded_console_engines(config)
        gpu_index = 0
        for row in loaded_rows:
            if str(row.get('id') or '') == server_id:
                gpu_index = int(row.get('gpu_index') or 0)
                break
        gpu_row = gpu_map.get(gpu_index)
        with _LOCK:
            track = _STALL_TRACK.setdefault(server_id, {})
            stalled = detect_generation_stall(stats, track, now=now, gpu_saturated=gpu_is_saturated(gpu_row))
        if stalled:
            stalled_servers.append((server_id, gpu_index, stats))

    if not stalled_servers:
        return {'success': True, 'enabled': True, 'actions': actions, 'reloads': reloads}

    stalled_servers.sort(
        key=lambda item: float(item[2].get('generating_seconds') or 0.0),
        reverse=True,
    )

    for _ in range(MAX_ACTIONS_PER_TICK):
        if not stalled_servers:
            break
        stalled_server_id, stalled_gpu_index, _stats = stalled_servers[0]
        candidates = _relief_candidates(
            config,
            stalled_server_id=stalled_server_id,
            stalled_gpu_index=stalled_gpu_index,
            generating_ids=generating_ids,
        )
        if not candidates:
            break
        victim = candidates[0]
        victim_id = str(victim.get('id') or '')
        estimated_gb = float(victim.get('estimated_gb') or 0.0)
        alt_gpu = _pick_alternate_gpu(
            config,
            exclude_index=stalled_gpu_index,
            required_gb=estimated_gb,
            gpus=gpus,
        )
        if alt_gpu is not None:
            _set_server_gpu_device(config, victim_id, alt_gpu)

        unload_result = unload_console_engine_for_relief(victim_id, cfg=config)
        _LAST_RELIEF_AT[victim_id] = now
        action = {
            'type': 'unload',
            'stalled_server_id': stalled_server_id,
            'unloaded_server_id': victim_id,
            'gpu_index': stalled_gpu_index,
            'alternate_gpu': alt_gpu,
            'success': bool(unload_result.get('success')),
            'error': unload_result.get('error'),
        }
        actions.append(action)
        if unload_result.get('success') and alt_gpu is not None:
            _queue_relocate_reload(
                server_id=victim_id,
                target_gpu=alt_gpu,
                wait_server_id=stalled_server_id,
                reason='gpu_saturation_relief',
            )
        logger.warning(
            'gpu relief unloaded %s on GPU %s for stalled generation on %s (alt_gpu=%s success=%s)',
            victim_id,
            stalled_gpu_index,
            stalled_server_id,
            alt_gpu,
            unload_result.get('success'),
        )

    return {'success': True, 'enabled': True, 'actions': actions, 'reloads': reloads}


def start_gpu_relief_watchdog() -> None:
    global _WATCHDOG_STARTED
    with _LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def run() -> None:
        time.sleep(8.0)
        while True:
            try:
                tick_gpu_relief()
            except Exception as exc:
                logger.exception('gpu relief watchdog failed: %s', exc)
            time.sleep(TICK_INTERVAL_SECONDS)

    threading.Thread(target=run, daemon=True, name='gpu-relief-watchdog').start()
