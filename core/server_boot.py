"""Spawn and track llama-server via start_llama_server.ps1."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.config import (
    get_dflash_root,
    is_embedding_server,
    normalize_hardware_settings,
    normalize_inference_settings,
    normalize_load_settings,
    normalize_server,
)
from core.gpu_devices import resolve_role_gpu_launch_params
from core.log_utils import rotate_log
from core.model_presets import infer_profile_from_path, model_id_from_path, preset_path_for, write_server_preset
from core.runtimes import runtime_process_identity_tokens

_started_launch: dict[int, dict[str, Any]] = {}
_started_processes: dict[int, subprocess.Popen] = {}
_boot_lock = threading.Lock()
_port_locks: dict[int, threading.RLock] = {}
_port_locks_guard = threading.Lock()
_boot_attempt_at: dict[int, float] = {}

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / 'logs'


def register_started_launch(port: int, signature: dict[str, Any]) -> None:
    _started_launch[int(port)] = dict(signature)


def register_started_process(port: int, process: subprocess.Popen) -> None:
    with _port_locks_guard:
        _started_processes[int(port)] = process


def get_started_process(port: int) -> subprocess.Popen | None:
    with _port_locks_guard:
        process = _started_processes.get(int(port))
        if process is not None and process.poll() is not None:
            _started_processes.pop(int(port), None)
            return None
        return process


def forget_started_process(port: int) -> None:
    with _port_locks_guard:
        _started_processes.pop(int(port), None)


def terminate_process_tree(process: subprocess.Popen | None) -> bool:
    if process is None or process.poll() is not None:
        return False
    try:
        if sys.platform == 'win32':
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(process.pid, 15)
        process.wait(timeout=10)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def terminate_started_process(port: int) -> bool:
    process = get_started_process(port)
    result = terminate_process_tree(process)
    forget_started_process(port)
    return result


def _port_lock(port: int) -> threading.RLock:
    with _port_locks_guard:
        return _port_locks.setdefault(int(port), threading.RLock())


def port_lock_for(port: int) -> threading.RLock:
    return _port_lock(port)


def wait_for_port_closed(host: str, port: int, *, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        if not _tcp_port_open(host, port):
            return True
        time.sleep(0.1)
    return not _tcp_port_open(host, port)


def _cleanup_failed_process(port: int, host: str, process: subprocess.Popen | None = None) -> None:
    terminate_process_tree(process or get_started_process(port))
    forget_started_process(port)
    wait_for_port_closed(host, port)


def managed_process_identity(pid: int) -> bool:
    """Return whether a Windows process looks like a Console-managed engine.

    Matches the shared runtime process-identity token set (llama-server by
    default, plus tokens contributed by registered adapters such as Piper).
    """
    if sys.platform != 'win32':
        return True
    query = (
        f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" "
        "| Select-Object Name,CommandLine | ConvertTo-Json -Compress)"
    )
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', query],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        import json

        details = json.loads(result.stdout)
        command = str(details.get('CommandLine') or '').lower()
        name = str(details.get('Name') or '').lower()
        tokens = tuple(str(token).lower() for token in runtime_process_identity_tokens())
        return any(token in name or token in command for token in tokens)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return False


def get_started_launch(port: int) -> dict[str, Any]:
    return dict(_started_launch.get(int(port)) or {})


def adopt_running_engine(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Track an engine process Console did not spawn (e.g. after Console restart)."""
    from core.runtime import _fetch_models_payload

    entry = normalize_server(server)
    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    if port <= 0:
        return {'success': False, 'adopted': False, 'error': 'invalid port'}
    if not _tcp_port_open(host, port):
        return {'success': False, 'adopted': False, 'port': port}
    pid = listener_pid(host, port)
    if pid is None or not managed_process_identity(pid):
        return {
            'success': False,
            'adopted': False,
            'port': port,
            'error': 'configured port is open but is not owned by a managed llama engine',
        }
    api_url = str(entry.get('api_url') or '').strip()
    if not api_url or not _fetch_models_payload(api_url):
        return {
            'success': False,
            'adopted': False,
            'port': port,
            'error': 'configured port is open but does not expose a compatible model API',
        }

    launch = resolve_role_gpu_launch_params(
        entry.get('gpu_device'),
        model_id=entry.get('model_id'),
        hardware=(cfg or {}).get('hardware_settings'),
        context_size=entry.get('context_size'),
    )
    if is_embedding_server(entry):
        from core.embedding_server import _launch_signature as embedding_launch_signature

        signature = embedding_launch_signature(entry, launch)
    else:
        signature = _launch_signature(entry, launch, cfg=cfg)
    _started_launch[port] = dict(signature)
    note_boot_cycle_end(port)
    return {'success': True, 'adopted': True, 'port': port}


def clear_server_tracking(port: int) -> None:
    _started_launch.pop(int(port), None)
    _boot_attempt_at.pop(int(port), None)
    forget_started_process(port)


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


def listener_pid(host: str, port: int) -> int | None:
    if port <= 0:
        return None
    try:
        if sys.platform == 'win32':
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'tcp'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            needle = f':{int(port)}'
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) < 5 or 'LISTENING' not in parts[3]:
                    continue
                local_addr = parts[1]
                if not local_addr.endswith(needle):
                    continue
                if local_addr.startswith(('127.0.0.1', '0.0.0.0', '[::]')):
                    try:
                        return int(parts[4])
                    except (ValueError, IndexError):
                        return None
        else:
            result = subprocess.run(
                ['lsof', '-ti', f'tcp:{int(port)}'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.stdout.strip():
                return int(result.stdout.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    return None


def _launch_signature(
    server: dict[str, Any],
    launch: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    idle_minutes = int(server.get('idle_unload_minutes') or 0)
    # Idle unload applies only to this Console-owned llama-server listener — never
    # external apps (LM Studio, Ollama, etc.).
    idle_seconds = 0 if idle_minutes <= 0 else idle_minutes * 60
    load = normalize_load_settings(server.get('load_settings'))
    hardware = normalize_hardware_settings((cfg or {}).get('hardware_settings'))
    infer = normalize_inference_settings(server.get('inference_settings'))
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
        'parallel_slots': int(load.get('parallel_slots') or 4),
        'offload_kv_cache_to_gpu': hardware.get('offload_kv_cache_to_gpu') is not False,
        'model_id': str(server.get('model_id') or ''),
        'reasoning_effort': str(infer.get('reasoning_effort') or 'auto'),
        'router_mode': True,
    }


def _spawn_detached(
    cmd: list[str],
    *,
    log_path: Path,
    cwd: Path,
    handle_key: str = '',
    port: int | None = None,
) -> subprocess.Popen:
    """Start engine; break away from Console job so restarts do not kill it."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open('a', encoding='utf-8')
    popen_kwargs: dict[str, Any] = {
        'cwd': str(cwd),
        'stdout': log_file,
        'stderr': subprocess.STDOUT,
        'stdin': subprocess.DEVNULL,
    }
    if sys.platform == 'win32':
        create_no_window = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        create_breakaway_from_job = 0x01000000
        popen_kwargs['creationflags'] = create_no_window | create_breakaway_from_job
    else:
        popen_kwargs['start_new_session'] = True
    try:
        process = subprocess.Popen(cmd, **popen_kwargs)
    except Exception:
        log_file.close()
        raise
    log_file.close()
    if port is not None:
        register_started_process(port, process)
    return process


def _spawn_router(
    entry: dict[str, Any],
    *,
    preset_path: Path,
    signature: dict[str, Any],
    log_path: Path,
) -> subprocess.Popen:
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
        '-KvOffload',
        'on' if signature.get('offload_kv_cache_to_gpu', True) else 'off',
        '-Parallel',
        str(signature.get('parallel_slots') or 4),
    ]
    if signature['tensor_split']:
        cmd.extend(['-TensorSplit', signature['tensor_split']])
    reasoning_effort = str(signature.get('reasoning_effort') or 'auto')
    if reasoning_effort != 'auto':
        cmd.extend(['-ReasoningEffort', reasoning_effort])

    return _spawn_detached(
        cmd,
        log_path=log_path,
        cwd=dflash_root,
        handle_key=str(entry['id']),
        port=int(entry['port']),
    )


def _port_bindable(host: str, port: int) -> tuple[bool, str | None]:
    import socket

    if port <= 0:
        return False, 'invalid port'
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, int(port)))
        return True, None
    except OSError as exc:
        winerr = getattr(exc, 'winerror', None)
        if winerr == 10013 or 'access' in str(exc).lower():
            return False, (
                f'Port {port} is blocked by Windows (Hyper-V reserved range). '
                f'Change the engine port in config.json — try 8301+ or 8088.'
            )
        if _tcp_port_open(host, port):
            return False, f'Port {port} is already in use'
        return False, f'Port {port} cannot bind: {exc}'


def _ensure_listener_port_free(entry: dict[str, Any]) -> bool:
    from core.runtime import stop_server

    port = int(entry.get('port') or 0)
    host = str(entry.get('host') or '127.0.0.1')
    api_url = str(entry.get('api_url') or '')
    if port <= 0 or not _tcp_port_open(host, port):
        return True
    pid = listener_pid(host, port)
    if pid is None or not managed_process_identity(pid):
        return False
    result = stop_server(port=port, host=host, api_url=api_url)
    return bool(result.get('success')) and wait_for_port_closed(host, port)


def start_router_listener(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    skip_preset_write: bool = False,
) -> dict[str, Any]:
    """Start one router listener at a time for each configured port."""
    entry = normalize_server(server)
    port = int(entry.get('port') or 0)
    if port <= 0 or not entry.get('id'):
        return {'success': False, 'error': 'invalid server'}
    lock = _port_lock(port)
    if not lock.acquire(blocking=False):
        return {'success': False, 'error': 'boot already in progress', 'port': port}
    try:
        return _start_router_listener_locked(entry, cfg=cfg, skip_preset_write=skip_preset_write)
    finally:
        lock.release()


def _start_router_listener_locked(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    skip_preset_write: bool = False,
) -> dict[str, Any]:
    """Start llama-server router on the configured port without loading a model."""
    entry = normalize_server(server)
    server_id = entry['id']
    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    model_id = str(entry.get('model_id') or '').strip()
    if port <= 0 or not server_id:
        return {'success': False, 'error': 'invalid server'}

    bindable, bind_error = _port_bindable(host, port)
    if not bindable:
        from core.load_progress import mark_boot_failed

        mark_boot_failed(server_id, bind_error or f'port {port} unavailable')
        note_boot_cycle_end(port)
        return {'success': False, 'error': bind_error or f'port {port} unavailable', 'port': port}

    launch = resolve_role_gpu_launch_params(
        entry.get('gpu_device'),
        model_id=model_id,
        hardware=(cfg or {}).get('hardware_settings'),
        context_size=entry.get('context_size'),
    )
    signature = _launch_signature(entry, launch, cfg=cfg)

    try:
        if skip_preset_write:
            preset_path = preset_path_for(server_id)
            if not preset_path.is_file():
                raise ValueError(f'preset not found: {preset_path}')
        else:
            preset_path = write_server_preset(entry, cfg=cfg)
    except ValueError as exc:
        return {'success': False, 'error': str(exc), 'port': port}

    dflash_root = get_dflash_root(cfg)
    script = dflash_root / 'scripts' / 'start_llama_server.ps1'
    if not script.is_file():
        return {'success': False, 'error': f'start script not found: {script}'}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'{server_id}.log'
    if not _ensure_listener_port_free(entry):
        return {'success': False, 'error': f'could not free port {port}', 'port': port}
    rotate_log(log_path)
    with log_path.open('a', encoding='utf-8') as log_file:
        log_file.write(f"\n=== boot {time.strftime('%Y-%m-%d %H:%M:%S')} profile={entry['profile']} router=1 idle=1 ===\n")
        log_file.flush()

    process: subprocess.Popen | None = None
    try:
        process = _spawn_router(entry, preset_path=preset_path, signature=signature, log_path=log_path)
    except Exception as exc:
        _cleanup_failed_process(port, host, process)
        return {'success': False, 'error': str(exc), 'port': port}

    from core.load_progress import boot_failure_message, mark_boot_failed, read_log_tail

    for _ in range(120):
        if _tcp_port_open(host, port):
            with log_path.open('a', encoding='utf-8') as ready_log:
                ready_log.write(f"=== router idle ready {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            _started_launch[port] = dict(signature)
            note_boot_cycle_end(port)
            return {'success': True, 'port': port, 'log_file': str(log_path), 'router_idle': True}
        failure = boot_failure_message(read_log_tail(server_id))
        if failure:
            mark_boot_failed(server_id, failure)
            _cleanup_failed_process(port, host, process)
            note_boot_cycle_end(port)
            return {'success': False, 'error': failure, 'port': port, 'log_file': str(log_path)}
        time.sleep(1.0)

    mark_boot_failed(server_id, f'timed out waiting for port {port}')
    _cleanup_failed_process(port, host, process)
    note_boot_cycle_end(port)
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


def load_server_checkpoint(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    model_path: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Ensure router is listening, then load the configured or ad-hoc checkpoint."""
    from core.config import is_embedding_server
    from core.embedding_server import start_embedding_server

    entry = normalize_server(server)
    if is_embedding_server(entry):
        return start_embedding_server(entry, cfg=cfg)

    from core.runtime import _fetch_models_payload, load_model, probe_models, stop_server

    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    api_url = str(entry.get('api_url') or '')
    custom_path = str(model_path or '').strip()
    if custom_path:
        path_obj = Path(custom_path)
        if not path_obj.is_file():
            return {'success': False, 'error': f'model file not found: {custom_path}'}
        from core.ocr_setup import llama_server_supports_glmocr, ocr_load_hints

        ocr_hints = ocr_load_hints(custom_path, cfg=cfg)
        if not ocr_hints.get('success'):
            return {'success': False, 'error': ocr_hints.get('error') or 'OCR setup failed', 'port': port}
        preset_entry = dict(entry)
        if ocr_hints.get('ocr'):
            if not llama_server_supports_glmocr():
                return {
                    'success': False,
                    'error': (
                        'GLM-OCR needs a newer llama-server build (b10405+). '
                        'Update llama.cpp under DFlash Console, then try again.'
                    ),
                    'port': port,
                }
            mmproj_path = str(ocr_hints.get('mmproj_path') or '').strip()
            if mmproj_path:
                preset_entry['mmproj_path'] = mmproj_path
            if ocr_hints.get('context_size'):
                preset_entry['context_size'] = int(ocr_hints['context_size'])
            load_settings = dict(preset_entry.get('load_settings') or {})
            load_settings['flash_attention'] = False
            preset_entry['load_settings'] = load_settings
        load_id = str(model_id or model_id_from_path(path_obj)).strip()
        profile_source = str(model_id or path_obj)
        load_profile = infer_profile_from_path(profile_source)
        try:
            write_server_preset(
                preset_entry,
                cfg=cfg,
                target_path=custom_path,
                model_id=load_id,
                profile=load_profile,
                use_draft=False,
            )
        except ValueError as exc:
            return {'success': False, 'error': str(exc), 'port': port}

        if _tcp_port_open(host, port):
            adopted = adopt_running_engine(entry, cfg=cfg)
            if not adopted.get('success'):
                return {'success': False, 'error': adopted.get('error') or f'port {port} is not a managed model API'}
            loaded = probe_models(api_url)
            if load_id in loaded:
                note_boot_cycle_end(port)
                return {'success': True, 'port': port, 'loaded': True, 'already_loaded': True, 'model': load_id}
            stop_server(port=port, host=host, api_url=api_url)

        listen = start_router_listener(entry, cfg=cfg, skip_preset_write=True)
        if not listen.get('success'):
            return listen

        load_result = load_model(api_url=api_url, model_id=load_id)
        if load_result.get('success'):
            note_boot_cycle_end(port)
            return {'success': True, 'port': port, 'loaded': True, 'model': load_id, 'adhoc': True}
        return {
            'success': False,
            'error': load_result.get('error') or 'model load failed',
            'port': port,
        }
    else:
        load_id = str(entry.get('model_id') or '').strip()
        if not load_id:
            return {'success': False, 'error': 'model_id required'}
        try:
            write_server_preset(entry, cfg=cfg)
        except ValueError as exc:
            return {'success': False, 'error': str(exc), 'port': port}

    if port <= 0:
        return {'success': False, 'error': 'invalid port'}

    registered_ids = {
        str(row.get('id') or row.get('model') or '').strip()
        for row in _fetch_models_payload(api_url)
    }
    registered_ids.discard('')

    if _tcp_port_open(host, port):
        adopted = adopt_running_engine(entry, cfg=cfg)
        if not adopted.get('success'):
            return {'success': False, 'error': adopted.get('error') or f'port {port} is not a managed model API'}
        loaded = probe_models(api_url)
        if load_id in loaded:
            note_boot_cycle_end(port)
            return {'success': True, 'port': port, 'loaded': True, 'already_loaded': True, 'model': load_id}
        if load_id not in registered_ids:
            stop_server(port=port, host=host, api_url=api_url)
    else:
        listen = start_router_listener(entry, cfg=cfg)
        if not listen.get('success'):
            return listen

    if not _tcp_port_open(host, port):
        listen = start_router_listener(entry, cfg=cfg)
        if not listen.get('success'):
            return listen

    load_result = load_model(api_url=api_url, model_id=load_id)
    if load_result.get('success'):
        note_boot_cycle_end(port)
        return {'success': True, 'port': port, 'loaded': True, 'model': load_id, 'adhoc': bool(custom_path)}
    return {
        'success': False,
        'error': load_result.get('error') or 'model load failed',
        'port': port,
    }


def start_server(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = normalize_server(server)
    port = int(entry.get('port') or 0)
    if port <= 0 or not entry.get('id'):
        return {'success': False, 'error': 'invalid server'}
    lock = _port_lock(port)
    if not lock.acquire(blocking=False):
        return {'success': False, 'error': 'boot already in progress', 'port': port}
    try:
        return _start_server_locked(entry, cfg=cfg)
    finally:
        lock.release()


def _start_server_locked(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.config import is_embedding_server
    from core.embedding_server import start_embedding_server
    from core.runtime import _fetch_models_payload, load_model, probe_models, router_unload_available, stop_server

    entry = normalize_server(server)
    if is_embedding_server(entry):
        return start_embedding_server(entry, cfg=cfg)

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
        context_size=entry.get('context_size'),
    )
    signature = _launch_signature(entry, launch, cfg=cfg)
    port_open = _tcp_port_open(host, port)
    if port_open:
        pid = listener_pid(host, port)
        if pid is None or not managed_process_identity(pid):
            return {
                'success': False,
                'error': f'port {port} is in use by an unmanaged process',
                'port': port,
            }
    if port_open and not _fetch_models_payload(api_url):
        return {
            'success': False,
            'error': f'port {port} is in use by a server that does not expose the configured model API',
            'port': port,
        }
    loaded = probe_models(api_url) if port_open else []

    if port_open and not router_unload_available(api_url):
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
        note_boot_cycle_end(port)
        return {'success': False, 'error': str(exc), 'port': port}

    dflash_root = get_dflash_root(cfg)
    script = dflash_root / 'scripts' / 'start_llama_server.ps1'
    if not script.is_file():
        note_boot_cycle_end(port)
        return {'success': False, 'error': f'start script not found: {script}'}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'{server_id}.log'
    if not _ensure_listener_port_free(entry):
        note_boot_cycle_end(port)
        return {'success': False, 'error': f'could not free port {port}', 'port': port}
    rotate_log(log_path)
    with log_path.open('a', encoding='utf-8') as log_file:
        log_file.write(f"\n=== boot {time.strftime('%Y-%m-%d %H:%M:%S')} profile={entry['profile']} router=1 ===\n")
        log_file.flush()

    process: subprocess.Popen | None = None
    try:
        process = _spawn_router(entry, preset_path=preset_path, signature=signature, log_path=log_path)
    except Exception as exc:
        _cleanup_failed_process(port, host, process)
        return {'success': False, 'error': str(exc), 'port': port}

    from core.load_progress import boot_failure_message, mark_boot_failed, read_log_tail

    for _ in range(120):
        if _tcp_port_open(host, port):
            time.sleep(1.0)
            load_result = load_model(api_url=api_url, model_id=model_id)
            if load_result.get('success'):
                _started_launch[port] = dict(signature)
                note_boot_cycle_end(port)
                return {'success': True, 'port': port, 'log_file': str(log_path), 'loaded': True}
            mark_boot_failed(server_id, load_result.get('error') or 'model load failed')
            _cleanup_failed_process(port, host, process)
            note_boot_cycle_end(port)
            return {
                'success': False,
                'error': load_result.get('error') or 'model load failed',
                'port': port,
                'log_file': str(log_path),
            }
        failure = boot_failure_message(read_log_tail(server_id))
        if failure:
            mark_boot_failed(server_id, failure)
            _cleanup_failed_process(port, host, process)
            note_boot_cycle_end(port)
            return {'success': False, 'error': failure, 'port': port, 'log_file': str(log_path)}
        time.sleep(1.0)

    mark_boot_failed(server_id, f'timed out waiting for port {port}')
    _cleanup_failed_process(port, host, process)
    note_boot_cycle_end(port)
    return {'success': False, 'error': f'timed out waiting for port {port}', 'port': port, 'log_file': str(log_path)}


def reload_server(server: dict[str, Any]) -> dict[str, Any]:
    from core.config import is_embedding_server
    from core.embedding_server import start_embedding_server, stop_embedding_server

    entry = normalize_server(server)
    if is_embedding_server(entry):
        stop_result = stop_embedding_server(entry)
        if not stop_result.get('success'):
            return stop_result
        return start_embedding_server(entry)

    from core.runtime import stop_server

    result = stop_server(port=int(entry['port']), host=str(entry['host']), api_url=entry.get('api_url'))
    if not result.get('success'):
        return result
    return start_server(entry)
