"""On-demand installer for the optional FreeToken WSL runtime."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Any

from core.config import ROOT
from core.runtime_install_job import (
    apply_job_progress,
    job_snapshot,
    parse_install_line,
    run_install_process,
    start_progress_heartbeat,
)

_BUNDLE = ROOT / 'runtimes' / 'freetoken'
_SCRIPT = ROOT / 'scripts' / 'install-freetoken-runtime.ps1'
_STATE_LOCK = threading.Lock()
_JOB: dict[str, Any] = {}


def bundle_paths() -> dict[str, str]:
    return {
        'bundle': str(_BUNDLE),
        'manifest': str(_BUNDLE / 'manifest.json'),
        'process_state': str(_BUNDLE / 'process.json'),
        'install_script': str(_SCRIPT),
    }


def is_installed() -> bool:
    try:
        from core.runtimes.freetoken import FreeTokenRuntimeAdapter

        return FreeTokenRuntimeAdapter.is_installed()
    except Exception:
        return False


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
        'backend': 'wsl',
        **job_snapshot(job),
        **bundle_paths(),
    }


def _install_worker() -> None:
    started = time.time()
    apply_job_progress(
        _STATE_LOCK,
        _JOB,
        progress=4.0,
        message='Starting FreeToken WSL install',
        status='installing',
        error='',
    )
    with _STATE_LOCK:
        _JOB['started_at'] = started
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
        code = run_install_process(cmd, cwd=str(ROOT), timeout=7200, on_line=on_line)
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

    installed = is_installed()
    with _STATE_LOCK:
        _JOB.update({
            'status': 'installed' if installed else 'error',
            'progress': 100.0 if installed else float(_JOB.get('progress') or 90.0),
            'message': 'FreeToken is ready' if installed else 'Install finished but manifest is missing',
            'error': '' if installed else 'FreeToken install finished without a manifest',
            'finished_at': time.time(),
        })


def start_install(*, backend: str = 'wsl') -> dict[str, Any]:
    requested = str(backend or 'wsl').strip().lower()
    if requested not in {'auto', 'wsl'}:
        return {'success': False, 'error': 'FreeToken currently supports WSL installation on Windows only'}
    with _STATE_LOCK:
        in_progress = str(_JOB.get('status') or '') == 'installing'
        already_installed = is_installed()
        if not in_progress and not already_installed:
            _JOB.clear()
            _JOB.update({'status': 'installing', 'progress': 0.0, 'started_at': time.time(), 'backend': 'wsl'})
    if in_progress:
        return {'success': False, 'error': 'install already in progress', **install_status()}
    if already_installed:
        return {'success': True, 'already_installed': True, **install_status()}
    thread = threading.Thread(target=_install_worker, daemon=True)
    thread.start()
    return {'success': True, 'started': True, **install_status()}


def _stop_runtime() -> None:
    try:
        from core.runtimes import get_runtime_adapter

        adapter = get_runtime_adapter('freetoken')
        if adapter is not None and callable(getattr(adapter, 'unload', None)):
            adapter.unload()
    except Exception:
        pass


def uninstall() -> dict[str, Any]:
    with _STATE_LOCK:
        in_progress = str(_JOB.get('status') or '') == 'installing'
    if in_progress:
        return {'success': False, 'error': 'install in progress', **install_status()}
    _stop_runtime()
    removed: list[str] = []
    for path in (_BUNDLE / 'manifest.json', _BUNDLE / 'process.json'):
        try:
            if path.is_file():
                path.unlink()
                removed.append(path.name)
        except OSError as exc:
            return {'success': False, 'error': str(exc), **install_status()}
    # The WSL venv is intentionally retained: removing a Linux environment
    # from Windows would require a destructive WSL command and is not needed
    # to disable the runtime. Reinstall safely reuses it.
    with _STATE_LOCK:
        _JOB.clear()
    return {
        'success': True,
        'runtime_id': 'freetoken',
        'removed': removed,
        'installed': is_installed(),
        'note': 'The WSL virtual environment was retained for safe reinstall.',
        **install_status(),
    }
