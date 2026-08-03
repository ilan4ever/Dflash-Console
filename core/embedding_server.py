"""Direct llama-server embedding engines (non-router mode)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config import get_dflash_root, is_embedding_server, normalize_load_settings, normalize_server
from core.gpu_devices import resolve_role_gpu_launch_params
from core.log_utils import rotate_log
from core.server_boot import (
    LOG_DIR,
    _spawn_detached,
    _tcp_port_open,
    clear_server_tracking,
    forget_started_process,
    get_started_process,
    note_boot_cycle_end,
    port_lock_for,
    register_started_launch,
    terminate_process_tree,
    wait_for_port_closed,
)

def llama_server_binary(*, cfg: dict[str, Any] | None = None) -> Path:
    root = get_dflash_root(cfg)
    binary = root / 'llama.cpp' / 'build' / 'bin' / 'Release' / 'llama-server.exe'
    if binary.is_file():
        return binary
    onevoice_root = str(os.environ.get('ONEVOICE_ROOT') or '').strip()
    if onevoice_root:
        fallback = Path(onevoice_root).expanduser() / '.tmp' / 'llama-b8418-win-cuda12' / 'llama-server.exe'
        if fallback.is_file():
            return fallback
    return binary


def resolve_embedding_model_path(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> Path:
    entry = normalize_server(server)
    target = str(entry.get('target_path') or '').strip()
    if target:
        path = Path(target)
        if path.is_file():
            return path
        raise ValueError(f'embedding model not found: {target}')

    onevoice_root = str(os.environ.get('ONEVOICE_ROOT') or '').strip()
    from core.model_paths import get_models_root

    models_root = get_models_root(cfg)
    candidates = [
        models_root / 'embeddings' / 'nomic-embed-text-v1.5.Q8_0.gguf',
        get_dflash_root(cfg) / 'models' / 'embeddings' / 'nomic-embed-text-v1.5.Q8_0.gguf',
    ]
    if onevoice_root:
        candidates.insert(
            1,
            Path(onevoice_root).expanduser() / 'models' / 'nomic-embed' / 'nomic-embed-text-v1.5.Q8_0.gguf',
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError('nomic embedding model not found; set target_path on the server entry')


def probe_embedding_health(host: str, port: int, *, timeout: float = 1.5) -> bool:
    if port <= 0:
        return False
    url = f'http://{host}:{int(port)}/health'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
            return str(payload.get('status') or '').lower() == 'ok'
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return False


def probe_embedding_models(api_url: str, *, timeout: float = 2.0) -> list[str]:
    url = str(api_url or '').strip().rstrip('/') + '/models'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return []
    ids: list[str] = []
    for key in ('data', 'models'):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get('id') or row.get('model') or row.get('name') or '').strip()
            state = row.get('status')
            if isinstance(state, dict):
                state = state.get('value')
            if state is not None and str(state).strip().lower() not in ('loaded', 'running'):
                continue
            if model_id:
                ids.append(model_id)
    return ids


def embedding_metadata(model_path: Path) -> dict[str, Any]:
    name = model_path.name
    hay = name.lower()
    quant = ''
    for token in ('Q8_0', 'Q4_K_M', 'Q4_0', 'F16'):
        if token.lower() in hay:
            quant = token
            break
    return {
        'model_kind': 'embedding',
        'architecture': 'nomic-bert',
        'model_family': 'nomic-embed-text',
        'model_version': 'v1.5',
        'pooling': 'mean',
        'embedding_dimensions': 768,
        'parameters': '137M',
        'quantization': quant or 'Q8_0',
        'context_tokens': 2048,
        'api_path': '/v1/embeddings',
    }


def _launch_signature(entry: dict[str, Any], launch: dict[str, Any]) -> dict[str, Any]:
    load = normalize_load_settings(entry.get('load_settings'))
    return {
        'context': int(entry.get('context_size') or 2048),
        'main_gpu': int(launch.get('main_gpu') or 0),
        'split_mode': str(launch.get('split_mode') or 'none'),
        'tensor_split': str(launch.get('tensor_split') or ''),
        'profile': str(entry.get('profile') or 'nomic-embed'),
        'gpu_layers': int(load.get('gpu_layers') or 99),
        'cpu_threads': int(load.get('cpu_threads') or 4),
        'model_id': str(entry.get('model_id') or ''),
        'router_mode': False,
        'engine_mode': 'embedding',
        'pooling': str(entry.get('pooling') or 'mean'),
    }


def start_embedding_server(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = normalize_server(server)
    port = int(entry.get('port') or 0)
    if port <= 0 or not entry.get('id'):
        return {'success': False, 'error': 'invalid embedding server config'}
    lock = port_lock_for(port)
    if not lock.acquire(blocking=False):
        return {'success': False, 'error': 'boot already in progress', 'port': port}
    try:
        return _start_embedding_server_locked(entry, cfg=cfg)
    finally:
        lock.release()


def _start_embedding_server_locked(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from core.runtime import stop_server

    entry = normalize_server(server)
    if not is_embedding_server(entry):
        return {'success': False, 'error': 'not an embedding server'}

    server_id = str(entry.get('id') or '').strip()
    port = int(entry.get('port') or 0)
    host = str(entry.get('host') or '127.0.0.1')
    api_url = str(entry.get('api_url') or '')
    model_id = str(entry.get('model_id') or '').strip()
    if not server_id or port <= 0:
        return {'success': False, 'error': 'invalid embedding server config'}

    if _tcp_port_open(host, port) and probe_embedding_health(host, port):
        loaded = probe_embedding_models(api_url)
        if not model_id and loaded:
            model_id = loaded[0]
        note_boot_cycle_end(port)
        return {
            'success': True,
            'port': port,
            'already_running': True,
            'loaded': True,
            'model': model_id or None,
        }

    if _tcp_port_open(host, port):
        stop_result = stop_server(port=port, host=host, api_url=api_url)
        if not stop_result.get('success') or not wait_for_port_closed(host, port):
            return {
                'success': False,
                'error': stop_result.get('error') or f'could not free port {port}',
                'port': port,
            }

    try:
        model_path = resolve_embedding_model_path(entry, cfg=cfg)
    except ValueError as exc:
        return {'success': False, 'error': str(exc), 'port': port}

    binary = llama_server_binary(cfg=cfg)
    if not binary.is_file():
        return {'success': False, 'error': f'llama-server binary not found: {binary}', 'port': port}

    launch = resolve_role_gpu_launch_params(
        entry.get('gpu_device'),
        model_id=model_id or model_path.stem,
        hardware=(cfg or {}).get('hardware_settings'),
    )
    signature = _launch_signature(entry, launch)
    load = normalize_load_settings(entry.get('load_settings'))
    pooling = str(entry.get('pooling') or 'mean').strip() or 'mean'
    ctx = max(512, int(entry.get('context_size') or 2048))

    cmd = [
        str(binary),
        '-m', str(model_path),
        '--host', host,
        '--port', str(port),
        '--embedding',
        '--pooling', pooling,
        '-ngl', str(int(load.get('gpu_layers') or 99)),
        '-t', str(int(load.get('cpu_threads') or 4)),
        '--ctx-size', str(ctx),
        '--main-gpu', str(int(launch.get('main_gpu') or 0)),
        '--log-disable',
    ]
    split_mode = str(launch.get('split_mode') or 'none')
    if split_mode and split_mode != 'none':
        cmd.extend(['--split-mode', split_mode])
    tensor_split = str(launch.get('tensor_split') or '').strip()
    if tensor_split:
        cmd.extend(['--tensor-split', tensor_split])

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'{server_id}.log'
    rotate_log(log_path)
    with log_path.open('a', encoding='utf-8') as log_file:
        log_file.write(
            f"\n=== boot {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"profile={entry.get('profile') or 'nomic-embed'} embedding=1 "
            f"model={model_path.name} gpu_layers={load.get('gpu_layers')} ===\n"
        )

    process: subprocess.Popen | None = None
    try:
        process = _spawn_detached(
            cmd,
            log_path=log_path,
            cwd=binary.parent,
            handle_key=server_id,
            port=port,
        )
    except Exception as exc:
        terminate_process_tree(process or get_started_process(port))
        forget_started_process(port)
        return {'success': False, 'error': str(exc), 'port': port, 'log_file': str(log_path)}

    from core.load_progress import boot_failure_message, mark_boot_failed, read_log_tail

    for _ in range(60):
        if probe_embedding_health(host, port):
            loaded = probe_embedding_models(api_url)
            resolved_model = model_id or (loaded[0] if loaded else model_path.stem)
            register_started_launch(port, signature)
            note_boot_cycle_end(port)
            with log_path.open('a', encoding='utf-8') as ready_log:
                ready_log.write(f"=== embedding ready {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            return {
                'success': True,
                'port': port,
                'loaded': True,
                'model': resolved_model,
                'log_file': str(log_path),
                'embedding': True,
            }
        failure = boot_failure_message(read_log_tail(server_id))
        if failure:
            mark_boot_failed(server_id, failure)
            terminate_process_tree(process or get_started_process(port))
            forget_started_process(port)
            wait_for_port_closed(host, port)
            note_boot_cycle_end(port)
            return {'success': False, 'error': failure, 'port': port, 'log_file': str(log_path)}
        time.sleep(0.5)

    mark_boot_failed(server_id, f'timed out waiting for embedding server on port {port}')
    terminate_process_tree(process or get_started_process(port))
    forget_started_process(port)
    wait_for_port_closed(host, port)
    note_boot_cycle_end(port)
    return {
        'success': False,
        'error': f'timed out waiting for embedding server on port {port}',
        'port': port,
        'log_file': str(log_path),
    }


def stop_embedding_server(server: dict[str, Any]) -> dict[str, Any]:
    from core.runtime import stop_server

    entry = normalize_server(server)
    port = int(entry.get('port') or 0)
    host = str(entry.get('host') or '127.0.0.1')
    result = stop_server(port=port, host=host, api_url=str(entry.get('api_url') or ''))
    if result.get('success'):
        clear_server_tracking(port)
    return result
