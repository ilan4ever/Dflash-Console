"""Spawn and track llama-server via start_llama_server.ps1."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.config import get_dflash_root, normalize_load_settings, normalize_server
from core.gpu_devices import resolve_role_gpu_launch_params
from core.model_presets import write_server_preset

_started_launch: dict[int, dict[str, Any]] = {}
_boot_lock = threading.Lock()
_boot_attempt_at: dict[int, float] = {}
_log_handles: dict[str, Any] = {}

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / 'logs'


def get_started_launch(port: int) -> dict[str, Any]:
    return dict(_started_launch.get(int(port)) or {})


def clear_server_tracking(port: int) -> None:
    _started_launch.pop(int(port), None)
    _boot_attempt_at.pop(int(port), None)


def note_boot_cycle_end(port: int) -> None:
    """Clear stale boot-in-progress tracking after load completes or model eject."""
    _boot_attempt_at.pop(int(port), None)


def recent_boot_attempt(port: int, *, window_seconds: float = 120.0) -> bool:
    last = float(_boot_attempt_at.get(int(port)) or 0.0)
    return last > 0 and (time.time() - last) <= window_seconds


def _tcp_port_open(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def _launch_signature(server: dict[str, Any], launch: dict[str, Any]) -> dict[str, Any]:
    idle_minutes = int(server.get('idle_unload_minutes') or 0)
    idle_seconds = 0 if idle_minutes <= 0 else idle_minutes * 60
    load = normalize_load_settings(server.get('load_settings'))
    return {
        'context': int(server.get('context_size') or 8192),
        'main_gpu': int(launch.get('main_gpu') or 0),
        'split_mode': str(launch.get('split_mode') or 'none'),
        'tensor_split': str(launch.get('tensor_split') or ''),
        'idle_unload_seconds': idle_seconds,
        'profile': str(server.get('profile') or ''),
        'gpu_layers': int(load.get('gpu_layers') or 99),
        'cpu_threads': int(load.get('cpu_threads') or 9),
        'eval_batch_size': int(load.get('eval_batch_size') or 2048),
        'physical_batch_size': int(load.get('physical_batch_size') or 512),
        'flash_attention': bool(load.get('flash_attention', True)),
        'model_id': str(server.get('model_id') or ''),
        'router_mode': True,
    }


def _spawn_router(entry: dict[str, Any], *, preset_path: Path, signature: dict[str, Any], log_file) -> None:
    dflash_root = get_dflash_root()
    script = dflash_root / 'scripts' / 'start_llama_server.ps1'
    cmd = [
        'pwsh.exe',
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        str(script),
        '-RouterMode',
        '-ModelsPreset',
        str(preset_path),
        '-Port',
        str(entry['port']),
        '-HostAddress',
        str(entry['host']),
        '-ContextSize',
        str(signature['context']),
        '-IdleUnloadSeconds',
        str(signature['idle_unload_seconds']),
        '-MainGpu',
        str(signature['main_gpu']),
        '-SplitMode',
        str(signature['split_mode']),
        '-GpuLayers',
        str(signature['gpu_layers']),
        '-CpuThreads',
        str(signature['cpu_threads']),
        '-EvalBatch',
        str(signature['eval_batch_size']),
        '-PhysicalBatch',
        str(signature['physical_batch_size']),
        '-FlashAttention',
        'on' if signature['flash_attention'] else 'off',
    ]
    if signature['tensor_split']:
        cmd.extend(['-TensorSplit', signature['tensor_split']])

    popen_kwargs: dict[str, Any] = {
        'cwd': str(dflash_root),
        'stdout': log_file,
        'stderr': subprocess.STDOUT,
    }
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.Popen(cmd, **popen_kwargs)


def start_router_listener(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start llama-server router on the configured port without loading a model."""
    entry = normalize_server(server)
    server_id = entry['id']
    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    model_id = str(entry.get('model_id') or '').strip()
    if port <= 0 or not server_id:
        return {'success': False, 'error': 'invalid server'}

    launch = resolve_role_gpu_launch_params(
        entry.get('gpu_device'),
        model_id=model_id,
        hardware=(cfg or {}).get('hardware_settings'),
    )
    signature = _launch_signature(entry, launch)

    try:
        preset_path = write_server_preset(entry, cfg=cfg)
    except ValueError as exc:
        return {'success': False, 'error': str(exc), 'port': port}

    dflash_root = get_dflash_root(cfg)
    script = dflash_root / 'scripts' / 'start_llama_server.ps1'
    if not script.is_file():
        return {'success': False, 'error': f'start script not found: {script}'}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'{server_id}.log'
    log_file = log_path.open('a', encoding='utf-8')
    log_file.write(f"\n=== boot {time.strftime('%Y-%m-%d %H:%M:%S')} profile={entry['profile']} router=1 idle=1 ===\n")
    log_file.flush()
    _log_handles[server_id] = log_file

    try:
        _spawn_router(entry, preset_path=preset_path, signature=signature, log_file=log_file)
    except Exception as exc:
        return {'success': False, 'error': str(exc), 'port': port}

    for _ in range(120):
        if _tcp_port_open(host, port):
            log_file.write(f"=== router idle ready {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            log_file.flush()
            _started_launch[port] = dict(signature)
            note_boot_cycle_end(port)
            return {'success': True, 'port': port, 'log_file': str(log_path), 'router_idle': True}
        time.sleep(1.0)

    return {'success': False, 'error': f'timed out waiting for port {port}', 'port': port, 'log_file': str(log_path)}


def eject_to_router_idle(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stop a legacy direct -m server and bring up router mode with no model loaded."""
    from core.runtime import router_unload_available, stop_server

    entry = normalize_server(server)
    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    api_url = str(entry.get('api_url') or '')

    if port <= 0:
        return {'success': False, 'error': 'invalid port'}

    if router_unload_available(api_url):
        return {'success': False, 'error': 'router unload API already available'}

    stop_result = stop_server(port=port, host=host, api_url=api_url)
    if not stop_result.get('success'):
        return stop_result
    return start_router_listener(entry, cfg=cfg)


def start_server(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.runtime import load_model, probe_models, router_unload_available, stop_server

    entry = normalize_server(server)
    server_id = entry['id']
    if not server_id:
        return {'success': False, 'error': 'server id required'}

    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    api_url = str(entry.get('api_url') or '')
    model_id = str(entry.get('model_id') or '').strip()
    if port <= 0:
        return {'success': False, 'error': 'invalid port'}
    if not model_id:
        return {'success': False, 'error': 'model_id required'}

    launch = resolve_role_gpu_launch_params(
        entry.get('gpu_device'),
        model_id=model_id,
        hardware=(cfg or {}).get('hardware_settings'),
    )
    signature = _launch_signature(entry, launch)
    loaded = probe_models(api_url) if _tcp_port_open(host, port) else []

    if _tcp_port_open(host, port) and not router_unload_available(api_url):
        stop_server(port=port, host=host, api_url=api_url)
        loaded = []

    if _tcp_port_open(host, port):
        started = _started_launch.get(port) or {}
        if started and started != signature:
            stop_server(port=port, host=host, api_url=api_url)
        elif model_id in loaded:
            note_boot_cycle_end(port)
            return {'success': True, 'port': port, 'already_running': True, 'loaded': True}
        elif loaded:
            stop_server(port=port, host=host, api_url=api_url)
        else:
            load_result = load_model(api_url=api_url, model_id=model_id)
            if load_result.get('success'):
                _started_launch[port] = dict(signature)
                note_boot_cycle_end(port)
                return {'success': True, 'port': port, 'loaded': True, 'reused_listener': True}
            stop_server(port=port, host=host, api_url=api_url)

    now = time.time()
    with _boot_lock:
        last = float(_boot_attempt_at.get(port) or 0.0)
        if (now - last) < 20.0:
            return {'success': False, 'error': 'boot already in progress', 'port': port}
        _boot_attempt_at[port] = now

    try:
        preset_path = write_server_preset(entry, cfg=cfg)
    except ValueError as exc:
        return {'success': False, 'error': str(exc), 'port': port}

    dflash_root = get_dflash_root(cfg)
    script = dflash_root / 'scripts' / 'start_llama_server.ps1'
    if not script.is_file():
        return {'success': False, 'error': f'start script not found: {script}'}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'{server_id}.log'
    log_file = log_path.open('a', encoding='utf-8')
    log_file.write(f"\n=== boot {time.strftime('%Y-%m-%d %H:%M:%S')} profile={entry['profile']} router=1 ===\n")
    log_file.flush()
    _log_handles[server_id] = log_file

    try:
        _spawn_router(entry, preset_path=preset_path, signature=signature, log_file=log_file)
    except Exception as exc:
        return {'success': False, 'error': str(exc), 'port': port}

    for _ in range(120):
        if _tcp_port_open(host, port):
            time.sleep(1.0)
            load_result = load_model(api_url=api_url, model_id=model_id)
            if load_result.get('success'):
                _started_launch[port] = dict(signature)
                note_boot_cycle_end(port)
                return {'success': True, 'port': port, 'log_file': str(log_path), 'loaded': True}
            return {
                'success': False,
                'error': load_result.get('error') or 'model load failed',
                'port': port,
                'log_file': str(log_path),
            }
        time.sleep(1.0)

    return {'success': False, 'error': f'timed out waiting for port {port}', 'port': port, 'log_file': str(log_path)}


def reload_server(server: dict[str, Any]) -> dict[str, Any]:
    from core.runtime import stop_server

    entry = normalize_server(server)
    result = stop_server(port=int(entry['port']), host=str(entry['host']), api_url=entry.get('api_url'))
    if not result.get('success'):
        return result
    return start_server(entry)
