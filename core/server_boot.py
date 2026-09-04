"""Spawn and track llama-server via start_llama_server.ps1."""

from __future__ import annotations

import os
import re
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
from core.model_presets import (
    infer_profile_from_path,
    model_id_from_path,
    preset_path_for,
    profile_requires_draft,
    sanitize_preset_model_id,
    write_server_preset,
)
from core.runtimes import runtime_process_identity_tokens
from core.net_listeners import pid_listening_on_port

_started_launch: dict[int, dict[str, Any]] = {}
_started_processes: dict[int, subprocess.Popen] = {}
_boot_lock = threading.Lock()
_port_locks: dict[int, threading.RLock] = {}
_port_locks_guard = threading.Lock()
_boot_attempt_at: dict[int, float] = {}

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / 'logs'


def _resolve_pwsh_exe() -> str:
    override = str(os.environ.get('PWSH_PATH', '')).strip()
    if override and Path(override).is_file():
        return override
    candidates = [
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'PowerShell' / '7' / 'pwsh.exe',
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'PowerShell' / '7' / 'pwsh.exe',
        Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe',
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return 'powershell.exe'


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


def _windows_process_details(pid: int) -> tuple[str, str] | None:
    try:
        import psutil

        proc = psutil.Process(int(pid))
        name = str(proc.name() or '')
        try:
            command = ' '.join(str(part) for part in (proc.cmdline() or []))
        except (psutil.Error, OSError):
            command = ''
        return name, command
    except Exception:
        pass
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
            return None
        import json

        details = json.loads(result.stdout)
        if not isinstance(details, dict):
            return None
        return str(details.get('Name') or ''), str(details.get('CommandLine') or '')
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def managed_process_identity(pid: int) -> bool:
    """Return whether a Windows process looks like a Console-managed engine.

    Matches the shared runtime process-identity token set (llama-server by
    default, plus tokens contributed by registered adapters such as Piper).
    """
    if sys.platform != 'win32':
        return True
    details = _windows_process_details(pid)
    if details is None:
        return False
    name, command = details
    hay = f'{name} {command}'.lower()
    tokens = tuple(str(token).lower() for token in runtime_process_identity_tokens())
    return any(token in hay for token in tokens)


def listener_is_managed_engine(host: str, port: int) -> bool:
    """True when the process listening on ``port`` is a Console llama/runtime engine."""
    if int(port or 0) <= 0:
        return False
    if not _tcp_port_open(host, port):
        return False
    pid = listener_pid(host, port)
    return pid is not None and managed_process_identity(pid)


def _sync_server_listen_port(server: dict[str, Any], port: int, host: str) -> None:
    server['port'] = int(port)
    server['api_url'] = f'http://{host}:{int(port)}/v1'


def ensure_managed_listen_port(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep this engine on a port we can actually bind.

    A live port is not enough — Wondershare (and similar apps) often sit on
    8090. If a foreign process owns the configured port, move the profile to a
    free port and persist the change so chat can start llama-server.
    """
    from core.config import apply_server_listen_port, load_config, suggest_server_port

    entry = normalize_server(server)
    server_id = str(entry.get('id') or '').strip()
    host = str(entry.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    port = int(entry.get('port') or 0)
    if not server_id or port <= 0:
        return {'success': False, 'error': 'invalid server', 'port': port}

    if not _tcp_port_open(host, port):
        return {'success': True, 'port': port, 'reason': 'free'}
    if listener_is_managed_engine(host, port):
        return {'success': True, 'port': port, 'reason': 'ours'}

    config = cfg if cfg is not None else load_config()
    new_port = int(suggest_server_port(cfg=config))
    if new_port <= 0 or new_port == port:
        return {
            'success': False,
            'error': f'port {port} is in use by an unmanaged process',
            'port': port,
            'reason': 'foreign',
        }

    persist = False
    try:
        applied = apply_server_listen_port(server_id, new_port, cfg=config, persist=True)
        persist = bool(applied.get('success'))
    except ValueError:
        apply_server_listen_port(server_id, new_port, cfg=config, persist=False)
    _sync_server_listen_port(server, new_port, host)
    return {
        'success': True,
        'port': new_port,
        'previous_port': port,
        'reason': 'rebound',
        'persisted': persist,
        'api_url': str(server.get('api_url') or ''),
    }


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
    return pid_listening_on_port(int(port), host)


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
    except OSError as exc:
        # WinError 5 (ERROR_ACCESS_DENIED): the Console is running inside a
        # Windows Job object that does not allow breakaway (e.g. launched from
        # the Electron desktop shell). Retry without CREATE_BREAKAWAY_FROM_JOB
        # so engines can still start; the engine then shares our job lifetime.
        if sys.platform == 'win32' and getattr(exc, 'winerror', None) == 5 and popen_kwargs.get('creationflags'):
            popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            try:
                process = subprocess.Popen(cmd, **popen_kwargs)
            except Exception:
                log_file.close()
                raise
        else:
            log_file.close()
            raise
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
        _resolve_pwsh_exe(),
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
    port_info = ensure_managed_listen_port(server, cfg=cfg)
    if not port_info.get('success'):
        return port_info
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

    stack_check = validate_dflash_stack(entry, cfg=cfg)
    if not stack_check.get('valid'):
        return stack_check

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


def resolve_checkpoint_load_id(
    server: dict[str, Any],
    *,
    model_path: str | None = None,
    model_id: str | None = None,
) -> str:
    entry = normalize_server(server)
    custom_path = str(model_path or entry.get('adhoc_model_path') or '').strip()
    if custom_path:
        path_obj = Path(custom_path)
        if path_obj.is_file():
            return sanitize_preset_model_id(model_id or model_id_from_path(path_obj), path_obj)
    if model_id:
        return sanitize_preset_model_id(model_id, entry.get('target_path'))
    return sanitize_preset_model_id(entry.get('model_id'), entry.get('target_path'))


def _checkpoint_tokens(value: str) -> set[str]:
    token = str(value or '').strip().lower().replace('\\', '/')
    if not token:
        return set()
    if token.startswith('library-file:'):
        token = token.split(':', 1)[1].strip()
    names = {token}
    base = token.rsplit('/', 1)[-1]
    if base:
        names.add(base)
        stem = base[:-5] if base.endswith('.gguf') else base
        names.add(stem)
        names.add(stem.replace('_', '-'))
    return {row for row in names if row}


def _checkpoint_id_loaded(load_id: str, loaded_ids: list[str]) -> bool:
    wanted = _checkpoint_tokens(load_id)
    if not wanted:
        return False
    have: set[str] = set()
    for row in loaded_ids:
        have |= _checkpoint_tokens(str(row or ''))
    return bool(wanted & have)


def _normalize_model_path(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve()).lower()
    except OSError:
        return str(path).replace('\\', '/').lower()


def resolve_load_target_path(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    model_path: str | None = None,
) -> str:
    custom = str(model_path or server.get('adhoc_model_path') or '').strip()
    if custom:
        return _normalize_model_path(custom)
    explicit = str(server.get('target_path') or '').strip()
    if explicit:
        return _normalize_model_path(explicit)
    from core.model_stack import resolve_model_stack

    for row in resolve_model_stack(server, cfg=cfg):
        if str(row.get('role') or '') == 'target' and row.get('path'):
            return _normalize_model_path(row['path'])
    return ''


_MIN_DFLASH2_ENGINE_BUILD = 10658
_ENGINE_BUILD_RE = re.compile(r'\bbuild\s+(\d+)\b', re.I)


def _llama_server_binary(cfg: dict[str, Any] | None = None) -> Path | None:
    """Find the engine using the same roots as the Console launcher."""
    roots = [get_dflash_root(cfg)]
    try:
        from core.model_paths import get_models_root

        # Installed shells keep the Console data root separate from the
        # developer checkout that owns models and llama.cpp.
        roots.append(get_models_root(cfg).parent)
    except (OSError, TypeError, ValueError):
        pass

    onevoice_root = str(os.environ.get('ONEVOICE_ROOT') or '').strip()
    candidates = [
        root / 'llama.cpp' / 'build' / 'bin' / 'Release' / binary_name
        for root in roots
        for binary_name in ('llama-server.exe', 'llama-server')
    ]
    if onevoice_root:
        candidates.extend([
            Path(onevoice_root) / '.tmp' / 'llama-b8418-win-cuda12' / 'llama-server.exe',
        ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def llama_server_capabilities(
    *,
    cfg: dict[str, Any] | None = None,
    binary: str | Path | None = None,
) -> dict[str, Any]:
    """Read the bundled engine build and expose supported DFlash generations."""
    engine = Path(binary).expanduser() if binary else _llama_server_binary(cfg)
    result: dict[str, Any] = {
        'available': bool(engine and engine.is_file()),
        'binary': str(engine) if engine else '',
        'version': '',
        'build': None,
        'dflash2': False,
    }
    if not engine or not engine.is_file():
        result['reason_code'] = 'engine-missing'
        result['message'] = 'The bundled llama-server engine is not installed.'
        return result
    try:
        completed = subprocess.run(
            [str(engine), '--version'],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result['reason_code'] = 'engine-unavailable'
        result['message'] = f'Could not inspect the bundled llama-server engine: {exc}'
        return result
    version = '\n'.join(
        part for part in (str(completed.stdout or ''), str(completed.stderr or '')) if part
    ).strip()
    result['version'] = version
    match = _ENGINE_BUILD_RE.search(version)
    if match:
        result['build'] = int(match.group(1))
        result['dflash2'] = result['build'] >= _MIN_DFLASH2_ENGINE_BUILD
        result['reason_code'] = 'ok' if result['dflash2'] else 'engine-too-old'
        result['message'] = (
            'The bundled llama-server supports DFlash2.'
            if result['dflash2']
            else (
                f'DFlash2 requires llama.cpp build {_MIN_DFLASH2_ENGINE_BUILD}+; '
                f'found build {result["build"]}.'
            )
        )
    else:
        result['reason_code'] = 'engine-version-unknown'
        result['message'] = 'The bundled llama-server build could not be identified.'
    return result


def _dflash_repair_result(
    entry: dict[str, Any],
    *,
    target_path: str = '',
    draft_path: str = '',
    reason_code: str,
    message: str,
    generation: str = '',
    preflight: dict[str, Any] | None = None,
    engine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_draft = draft_path if Path(draft_path).expanduser().is_file() else ''
    repair_action = 'attach_draft' if target_path else 'choose_target'
    result: dict[str, Any] = {
        'success': False,
        'valid': False,
        'required': True,
        'error': 'dflash_stack_repair_required',
        'message': message,
        'reason_code': reason_code,
        'server_id': str(entry.get('id') or ''),
        'target_path': target_path,
        'draft_path': draft_path,
        'dflash_generation': generation or 'dflash1',
        'repair': {
            'action': repair_action,
            'server_id': str(entry.get('id') or ''),
            'target_path': target_path,
            'current_draft_path': current_draft,
            'dflash_generation': generation or 'dflash1',
        },
    }
    if preflight is not None:
        result['preflight'] = preflight
    if engine is not None:
        result['engine'] = engine
        result['repair']['action'] = 'update_engine'
        result['update'] = {
            'action': 'update_engine',
            'required_engine': f'llama.cpp build {_MIN_DFLASH2_ENGINE_BUILD}+',
            'engine': engine,
        }
    return result


def validate_dflash_stack(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    model_path: str | None = None,
) -> dict[str, Any]:
    """Validate the target, draft, metadata pair, and DFlash engine requirement."""
    entry = normalize_server(server)
    if not profile_requires_draft(entry.get('profile')):
        return {'success': True, 'valid': True, 'required': False}

    from core.dflash_generation import infer_dflash_generation
    from core.model_stack import resolve_model_stack
    from core.stack_match import preflight_dflash_pair

    stack = resolve_model_stack(entry, cfg=cfg)
    target_row = next((row for row in stack if row.get('role') == 'target'), {})
    draft_row = next(
        (row for row in stack if str(row.get('role') or '').startswith('draft')),
        {},
    )
    target = str(model_path or entry.get('target_path') or target_row.get('path') or '').strip()
    draft = str(entry.get('draft_path') or draft_row.get('path') or '').strip()
    generation = infer_dflash_generation(draft)
    if not target:
        return _dflash_repair_result(
            entry,
            reason_code='target-required',
            message='This DFlash profile has no full target model. Choose the target in the stack wizard.',
            generation=generation,
        )
    target_file = Path(target).expanduser()
    if not target_file.is_file():
        return _dflash_repair_result(
            entry,
            target_path=target,
            reason_code='missing-target',
            message=f'DFlash target model file not found: {target}',
            generation=generation,
        )
    if not draft:
        return _dflash_repair_result(
            entry,
            target_path=str(target_file),
            reason_code='draft-required',
            message='This DFlash profile requires a matching draft accelerator before it can load.',
            generation=generation,
        )
    draft_file = Path(draft).expanduser()
    if not draft_file.is_file():
        return _dflash_repair_result(
            entry,
            target_path=str(target_file),
            draft_path=draft,
            reason_code='missing-draft',
            message=f'DFlash draft accelerator file not found: {draft}',
            generation=generation,
        )

    preflight = preflight_dflash_pair(target_file, draft_file)
    generation = str(preflight.get('dflash_generation') or generation or 'dflash1')
    if not preflight.get('compatible'):
        return _dflash_repair_result(
            entry,
            target_path=str(target_file),
            draft_path=str(draft_file),
            reason_code=str(preflight.get('reason_code') or 'incompatible-pair'),
            message=str(preflight.get('reason') or 'The configured DFlash target and draft are incompatible.'),
            generation=generation,
            preflight=preflight,
        )
    if not preflight.get('validated'):
        return _dflash_repair_result(
            entry,
            target_path=str(target_file),
            draft_path=str(draft_file),
            reason_code='preflight-unavailable',
            message='The target and draft metadata could not be validated. Choose a verified GGUF pair.',
            generation=generation,
            preflight=preflight,
        )
    if generation == 'dflash2':
        engine = llama_server_capabilities(cfg=cfg)
        if not engine.get('dflash2'):
            return _dflash_repair_result(
                entry,
                target_path=str(target_file),
                draft_path=str(draft_file),
                reason_code='engine-update-required',
                message=str(engine.get('message') or 'This engine cannot load DFlash2.'),
                generation=generation,
                preflight=preflight,
                engine=engine,
            )
    return {
        'success': True,
        'valid': True,
        'required': True,
        'server_id': str(entry.get('id') or ''),
        'target_path': str(target_file),
        'draft_path': str(draft_file),
        'dflash_generation': generation,
        'preflight': preflight,
    }


def dflash_live_launch_state(server: dict[str, Any]) -> bool | None:
    """Return whether the current router launch includes a draft argument."""
    if not profile_requires_draft(server.get('profile')):
        return None
    server_id = str(server.get('id') or '').strip()
    if not server_id:
        return None
    preset = preset_path_for(server_id)
    if not preset.is_file():
        return None
    try:
        return any(
            line.strip().lower().startswith('model-draft')
            for line in preset.read_text(encoding='utf-8').splitlines()
        )
    except OSError:
        return None


def find_target_loaded_elsewhere(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    model_path: str | None = None,
    exclude_server_id: str | None = None,
) -> dict[str, Any] | None:
    """True when the same GGUF target is already live on another Console engine."""
    from core.config import is_embedding_server, list_servers
    from core.runtime import build_server_status

    target = resolve_load_target_path(server, cfg=cfg, model_path=model_path)
    if not target:
        return None

    config = cfg or load_config()
    self_id = str(exclude_server_id or server.get('id') or '')
    for other in list_servers(config):
        other_id = str(other.get('id') or '')
        if not other_id or other_id == self_id or other.get('enabled', True) is False:
            continue
        if is_embedding_server(other):
            continue
        status = build_server_status(other, cfg=config)
        if status.get('status') != 'loaded' and not status.get('loaded_models'):
            continue
        other_target = resolve_load_target_path(other, cfg=config)
        if other_target and other_target == target:
            return {
                'server_id': other_id,
                'label': str(other.get('label') or other_id),
                'port': int(other.get('port') or 0),
                'target_path': target,
            }
    return None


def checkpoint_already_loaded(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    model_path: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a success payload when the requested checkpoint is already live."""
    from core.config import is_embedding_server

    entry = normalize_server(server)
    if is_embedding_server(entry):
        return None

    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    api_url = str(entry.get('api_url') or '')
    load_id = resolve_checkpoint_load_id(entry, model_path=model_path, model_id=model_id)
    if port <= 0 or not api_url or not load_id or not _tcp_port_open(host, port):
        return None

    from core.runtime import probe_models

    adopted = adopt_running_engine(entry, cfg=cfg)
    if not adopted.get('success'):
        return None
    loaded = probe_models(api_url)
    if not _checkpoint_id_loaded(load_id, loaded):
        return None
    note_boot_cycle_end(port)
    return {
        'success': True,
        'port': port,
        'loaded': True,
        'already_loaded': True,
        'model': load_id,
    }


def _draft_load_error(detail: Any) -> bool:
    text = str(detail or '').lower()
    return any(
        token in text
        for token in (
            'draft model',
            'model-draft',
            'failed to load draft',
            'wrong number of tensors',
            'invalid vector subscript',
        )
    )


def _explain_draft_load_error(detail: Any, draft_path: str | None = None) -> str:
    message = str(detail or 'model load failed')
    if _draft_load_error(message) and draft_path:
        from core.dflash_generation import infer_dflash_generation

        if infer_dflash_generation(draft_path) == 'dflash2':
            return (
                f'{message}. Qwen/Gemma DFlash 2 requires a llama.cpp build with '
                'DFlash2 support (PR #27342); update the bundled llama-server and retry.'
            )
    return message


def _structured_draft_load_failure(
    entry: dict[str, Any],
    stack_check: dict[str, Any],
    error_text: str,
) -> dict[str, Any] | None:
    if not profile_requires_draft(entry.get('profile')) or not _draft_load_error(error_text):
        return None
    return _dflash_repair_result(
        entry,
        target_path=str(stack_check.get('target_path') or entry.get('target_path') or ''),
        draft_path=str(stack_check.get('draft_path') or entry.get('draft_path') or ''),
        reason_code='draft-load-failed',
        message=_explain_draft_load_error(
            error_text,
            stack_check.get('draft_path') or entry.get('draft_path'),
        ),
        generation=str(stack_check.get('dflash_generation') or ''),
        preflight=stack_check.get('preflight'),
    )


def _checkpoint_load_failure_error(server_id: str, status_info: dict[str, Any]) -> str:
    from core.load_progress import model_load_failure_message, read_log_tail

    log_failure = model_load_failure_message(read_log_tail(server_id))
    if log_failure:
        return log_failure
    args = status_info.get('args') or []
    if args:
        return ' '.join(str(part) for part in args[-3:])
    return 'model load failed'


def _wait_for_checkpoint_load(
    *,
    api_url: str,
    load_id: str,
    host: str,
    port: int,
    server_id: str = '',
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    from core.runtime import _fetch_models_payload, _model_state

    deadline = time.time() + timeout_seconds
    load_id = str(load_id or '').strip()
    while time.time() < deadline:
        rows = _fetch_models_payload(api_url)
        match = None
        for row in rows:
            row_id = str(row.get('id') or row.get('model') or '').strip()
            if row_id == load_id:
                match = row
                break
        if match is None and rows:
            match = rows[0]
        if not match:
            time.sleep(1.0)
            continue
        status = _model_state(match)
        status_info = match.get('status') if isinstance(match.get('status'), dict) else {}
        if status == 'loaded':
            return {'status': 'loaded'}
        if status == 'unloaded' and status_info.get('failed'):
            return {
                'status': 'failed',
                'error': _checkpoint_load_failure_error(server_id, status_info),
            }
        time.sleep(1.0)
    return {'status': 'timeout', 'error': f'timed out waiting for {load_id} on {host}:{port}'}


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
    stack_check = validate_dflash_stack(entry, cfg=cfg, model_path=model_path)
    if not stack_check.get('valid'):
        return stack_check

    custom_path = str(model_path or '').strip()
    elsewhere = find_target_loaded_elsewhere(
        entry,
        cfg=cfg,
        model_path=custom_path or None,
        exclude_server_id=str(entry.get('id') or ''),
    )
    if elsewhere:
        host_label = str(elsewhere.get('label') or elsewhere.get('server_id') or 'another engine')
        port = int(elsewhere.get('port') or 0)
        port_text = f' (port {port})' if port else ''
        return {
            'success': False,
            'already_loaded_elsewhere': True,
            'error': (
                f'This model is already loaded on {host_label}{port_text}. '
                'Unload it before loading a second copy.'
            ),
            **elsewhere,
        }

    from core.runtime import _fetch_models_payload, load_model, probe_models, stop_server

    port = int(entry['port'] or 0)
    host = str(entry['host'] or '127.0.0.1')
    api_url = str(entry.get('api_url') or '')
    live_draft_before_preset = (
        dflash_live_launch_state(entry)
        if _tcp_port_open(host, port)
        else None
    )
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
        load_id = sanitize_preset_model_id(model_id or model_id_from_path(path_obj), path_obj)
        profile_source = str(path_obj)
        load_profile = (
            str(entry.get('profile') or '')
            if profile_requires_draft(entry.get('profile'))
            else infer_profile_from_path(profile_source)
        )
        preset_entry['target_path'] = custom_path
        # The router preset section must be registered under the ad-hoc load id,
        # not the engine's configured model_id — start_router_listener rewrites
        # the preset from this entry, and load_model() below asks for load_id.
        preset_entry['model_id'] = load_id
        preset_entry['profile'] = load_profile
        if not profile_requires_draft(load_profile):
            # Plain ad-hoc GGUF: never pair it with the engine's configured
            # DFlash draft when the preset is rewritten on listener start.
            preset_entry['draft_path'] = ''
        try:
            write_server_preset(
                preset_entry,
                cfg=cfg,
                target_path=custom_path,
                model_id=load_id,
                profile=load_profile,
                use_draft=None if profile_requires_draft(load_profile) else False,
            )
        except ValueError as exc:
            return {'success': False, 'error': str(exc), 'port': port}

        if _tcp_port_open(host, port):
            adopted = adopt_running_engine(entry, cfg=cfg)
            if not adopted.get('success'):
                return {'success': False, 'error': adopted.get('error') or f'port {port} is not a managed model API'}
            already = checkpoint_already_loaded(entry, cfg=cfg, model_path=custom_path, model_id=load_id)
            if already and (
                not profile_requires_draft(entry.get('profile'))
                or live_draft_before_preset is True
            ):
                return already
            stop_server(port=port, host=host, api_url=api_url)

        listen = start_router_listener(preset_entry, cfg=cfg)
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
        load_id = str(model_id or entry.get('model_id') or '').strip()
        if not load_id:
            return {'success': False, 'error': 'model_id required'}
        resolved_target = resolve_load_target_path(entry, cfg=cfg, model_path=model_path)
        if resolved_target and not str(entry.get('target_path') or '').strip():
            entry = {**entry, 'target_path': resolved_target}
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

    from core.chat_vision import router_registration_stale

    preset_stale = router_registration_stale(entry, load_id=load_id)

    if _tcp_port_open(host, port):
        adopted = adopt_running_engine(entry, cfg=cfg)
        if not adopted.get('success'):
            return {'success': False, 'error': adopted.get('error') or f'port {port} is not a managed model API'}
        already = checkpoint_already_loaded(entry, cfg=cfg, model_path=model_path, model_id=load_id)
        if already and (
            not profile_requires_draft(entry.get('profile'))
            or live_draft_before_preset is True
        ):
            return already
        if already or load_id not in registered_ids or preset_stale:
            stop_server(port=port, host=host, api_url=api_url)
    else:
        listen = start_router_listener(
            entry,
            cfg=cfg,
        )
        if not listen.get('success'):
            return listen

    if not _tcp_port_open(host, port):
        listen = start_router_listener(
            entry,
            cfg=cfg,
        )
        if not listen.get('success'):
            return listen

    def _attempt_load() -> dict[str, Any]:
        return load_model(api_url=api_url, model_id=load_id)

    load_result = _attempt_load()
    if load_result.get('success'):
        settled = _wait_for_checkpoint_load(
            api_url=api_url,
            load_id=load_id,
            host=host,
            port=port,
            server_id=str(entry.get('id') or ''),
        )
        if settled.get('status') == 'loaded':
            note_boot_cycle_end(port)
            return {'success': True, 'port': port, 'loaded': True, 'model': load_id, 'adhoc': bool(custom_path)}
        error_text = str(settled.get('error') or load_result.get('error') or '')
        structured_failure = _structured_draft_load_failure(entry, stack_check, error_text)
        if structured_failure:
            structured_failure['port'] = port
            return structured_failure
        return {
            'success': False,
            'error': _explain_draft_load_error(
                error_text or 'model load failed',
                entry.get('draft_path'),
            ),
            'port': port,
        }
    error_text = str(load_result.get('error') or 'model load failed')
    structured_failure = _structured_draft_load_failure(entry, stack_check, error_text)
    if structured_failure:
        structured_failure['port'] = port
        return structured_failure
    return {
        'success': False,
        'error': _explain_draft_load_error(
            error_text,
            entry.get('draft_path'),
        ),
        'port': port,
    }


def start_server(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    port_info = ensure_managed_listen_port(server, cfg=cfg)
    if not port_info.get('success'):
        return port_info
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
    stack_check = validate_dflash_stack(entry, cfg=cfg)
    if not stack_check.get('valid'):
        return stack_check

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
    port_info = ensure_managed_listen_port(entry, cfg=cfg)
    if not port_info.get('success'):
        return port_info
    if port_info.get('reason') == 'rebound':
        entry = normalize_server(entry)
        port = int(entry.get('port') or 0)
        api_url = str(entry.get('api_url') or '')
        signature = _launch_signature(entry, launch, cfg=cfg)
        _sync_server_listen_port(server, port, host)
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
    live_draft_before_preset = dflash_live_launch_state(entry) if port_open else None

    if port_open and not router_unload_available(api_url):
        stop_server(port=port, host=host, api_url=api_url)
        loaded = []

    if _tcp_port_open(host, port):
        started = _started_launch.get(port) or {}
        if started and started != signature:
            stop_server(port=port, host=host, api_url=api_url)
        elif model_id in loaded and (
            not profile_requires_draft(entry.get('profile'))
            or live_draft_before_preset is True
        ):
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


def reload_server(
    server: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from core.config import is_embedding_server
    from core.embedding_server import start_embedding_server, stop_embedding_server

    entry = normalize_server(server)
    if is_embedding_server(entry):
        stop_result = stop_embedding_server(entry)
        if not stop_result.get('success'):
            return stop_result
        return start_embedding_server(entry, cfg=cfg)

    from core.runtime import stop_server

    result = stop_server(port=int(entry['port']), host=str(entry['host']), api_url=entry.get('api_url'))
    if not result.get('success'):
        return result
    return start_server(entry, cfg=cfg)
