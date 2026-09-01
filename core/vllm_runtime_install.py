"""On-demand install helpers for the vLLM runtime bundle."""

from __future__ import annotations

import subprocess
import threading
import time
import shutil
from typing import Any

from core.config import ROOT
from core.runtime_install_job import (
    apply_job_progress,
    job_snapshot,
    parse_install_line,
    run_install_process,
    start_progress_heartbeat,
)

_BUNDLE = ROOT / 'runtimes' / 'vllm'
_SCRIPT = ROOT / 'scripts' / 'install-vllm-runtime.ps1'
_STATE_LOCK = threading.Lock()
_JOB: dict[str, Any] = {}


def bundle_paths() -> dict[str, str]:
    return {
        'bundle': str(_BUNDLE),
        'venv_python': str(_BUNDLE / 'venv' / 'Scripts' / 'python.exe'),
        'install_script': str(_SCRIPT),
        'manifest': str(_BUNDLE / 'manifest.json'),
    }


def is_installed() -> bool:
    try:
        from core.runtimes.vllm import VllmRuntimeAdapter

        return VllmRuntimeAdapter.is_installed()
    except Exception:
        return (_BUNDLE / 'venv' / 'Scripts' / 'python.exe').is_file()


def install_status() -> dict[str, Any]:
    if is_installed():
        with _STATE_LOCK:
            if _JOB:
                _JOB.clear()
    with _STATE_LOCK:
        job = dict(_JOB)
    installed = is_installed()
    status = str(job.get('status') or ('installed' if installed else 'idle'))
    if installed:
        status = 'installed'
    return {
        'installed': installed,
        'status': status,
        'error': '' if installed else str(job.get('error') or ''),
        'started_at': job.get('started_at'),
        'finished_at': job.get('finished_at'),
        'backend': str(job.get('backend') or ''),
        **job_snapshot(job),
        **bundle_paths(),
    }


def _install_worker(*, backend: str) -> None:
    started = time.time()
    apply_job_progress(
        _STATE_LOCK,
        _JOB,
        progress=4.0,
        message='Starting vLLM install',
        status='installing',
        error='',
    )
    with _STATE_LOCK:
        _JOB.update({'started_at': started, 'backend': backend})
    if not _SCRIPT.is_file():
        apply_job_progress(_STATE_LOCK, _JOB, status='error', error='install script missing')
        with _STATE_LOCK:
            _JOB['finished_at'] = time.time()
        return
    cmd = [
        'powershell',
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        str(_SCRIPT),
        '-Backend',
        str(backend or 'auto'),
    ]
    stop = threading.Event()
    start_progress_heartbeat(_STATE_LOCK, _JOB, started_at=started, stop=stop)
    log_tail: list[str] = []

    def on_line(line: str) -> None:
        progress, message = parse_install_line(line)
        cleaned = message or line.strip()
        if cleaned:
            log_tail.append(cleaned)
            if len(log_tail) > 40:
                del log_tail[: len(log_tail) - 40]
        apply_job_progress(
            _STATE_LOCK,
            _JOB,
            progress=progress,
            message=message,
            log_line=cleaned,
        )

    try:
        code = run_install_process(cmd, cwd=str(ROOT), timeout=5400, on_line=on_line)
    except (OSError, subprocess.SubprocessError) as exc:
        stop.set()
        apply_job_progress(_STATE_LOCK, _JOB, status='error', error=str(exc))
        with _STATE_LOCK:
            _JOB['finished_at'] = time.time()
        return
    stop.set()
    if code != 0:
        detail = '\n'.join(log_tail[-12:]).strip()
        apply_job_progress(
            _STATE_LOCK,
            _JOB,
            status='error',
            error=detail or f'install exited with code {code}',
        )
        with _STATE_LOCK:
            _JOB['finished_at'] = time.time()
        return
    ok = is_installed()
    verify_error = ''
    if not ok:
        try:
            from core.runtimes.vllm import verify_vllm_installation

            _ok, verify_error = verify_vllm_installation()
            ok = _ok
        except Exception as exc:
            verify_error = str(exc)
    with _STATE_LOCK:
        _JOB.update({
            'status': 'installed' if ok else 'error',
            'progress': 100.0 if ok else float(_JOB.get('progress') or 90.0),
            'message': 'vLLM is ready' if ok else 'Install finished but vLLM is not importable yet',
            'error': '' if ok else (verify_error or 'install finished but vLLM is not importable yet'),
            'finished_at': time.time(),
        })


def start_install(*, backend: str = 'auto') -> dict[str, Any]:
    with _STATE_LOCK:
        if str(_JOB.get('status') or '') == 'installing':
            return {'success': False, 'error': 'install already in progress', **install_status()}
        if is_installed():
            return {'success': True, 'already_installed': True, **install_status()}
        _JOB.clear()
        _JOB.update({'status': 'installing', 'progress': 0.0, 'started_at': time.time(), 'backend': backend})
    thread = threading.Thread(target=_install_worker, kwargs={'backend': backend}, daemon=True)
    thread.start()
    return {'success': True, 'started': True, **install_status()}


def _stop_runtime() -> None:
    try:
        from core.runtimes import get_runtime_adapter

        adapter = get_runtime_adapter('vllm')
        if adapter is None:
            return
        unload = getattr(adapter, 'unload', None)
        if callable(unload):
            unload()
    except Exception:
        pass


def uninstall() -> dict[str, Any]:
    with _STATE_LOCK:
        if str(_JOB.get('status') or '') == 'installing':
            return {'success': False, 'error': 'install in progress', **install_status()}
    _stop_runtime()
    removed: list[str] = []
    venv = _BUNDLE / 'venv'
    manifest = _BUNDLE / 'manifest.json'
    try:
        if venv.is_dir():
            shutil.rmtree(venv)
            removed.append('venv')
        if manifest.is_file():
            manifest.unlink()
            removed.append('manifest')
    except OSError as exc:
        return {'success': False, 'error': str(exc), **install_status()}
    with _STATE_LOCK:
        _JOB.clear()
    return {
        'success': True,
        'runtime_id': 'vllm',
        'removed': removed,
        'installed': is_installed(),
        **install_status(),
    }
