"""On-demand install helpers for the Transformers / PyTorch runtime bundle."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.config import ROOT

_BUNDLE = ROOT / 'runtimes' / 'transformers'
_SCRIPT = ROOT / 'scripts' / 'install-transformers-runtime.ps1'
_STATE_LOCK = threading.Lock()
_JOB: dict[str, Any] = {}


def bundle_paths() -> dict[str, str]:
    return {
        'bundle': str(_BUNDLE),
        'worker': str(_BUNDLE / 'server.py'),
        'venv_python': str(_BUNDLE / 'venv' / 'Scripts' / 'python.exe'),
        'install_script': str(_SCRIPT),
    }


def is_installed() -> bool:
    return (_BUNDLE / 'server.py').is_file() and (_BUNDLE / 'venv' / 'Scripts' / 'python.exe').is_file()


def install_status() -> dict[str, Any]:
    with _STATE_LOCK:
        job = dict(_JOB)
    return {
        'installed': is_installed(),
        'status': str(job.get('status') or ('installed' if is_installed() else 'idle')),
        'progress': job.get('progress'),
        'error': str(job.get('error') or ''),
        'started_at': job.get('started_at'),
        'finished_at': job.get('finished_at'),
        **bundle_paths(),
    }


def _install_worker(*, torch_variant: str) -> None:
    with _STATE_LOCK:
        _JOB.update({'status': 'installing', 'progress': 5.0, 'error': '', 'started_at': time.time()})
    if not _SCRIPT.is_file():
        with _STATE_LOCK:
            _JOB.update({'status': 'error', 'error': 'install script missing', 'finished_at': time.time()})
        return
    cmd = [
        'powershell',
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        str(_SCRIPT),
        '-TorchVariant',
        str(torch_variant or 'auto'),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        with _STATE_LOCK:
            _JOB.update({'status': 'error', 'error': str(exc), 'finished_at': time.time()})
        return
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()[:2000]
        with _STATE_LOCK:
            _JOB.update({
                'status': 'error',
                'error': detail or f'install exited with code {proc.returncode}',
                'finished_at': time.time(),
            })
        return
    with _STATE_LOCK:
        _JOB.update({
            'status': 'installed' if is_installed() else 'error',
            'progress': 100.0,
            'error': '' if is_installed() else 'install finished but venv is missing',
            'finished_at': time.time(),
        })


def start_install(*, torch_variant: str = 'auto') -> dict[str, Any]:
    with _STATE_LOCK:
        if str(_JOB.get('status') or '') == 'installing':
            return {'success': False, 'error': 'install already in progress', **install_status()}
        if is_installed():
            return {'success': True, 'already_installed': True, **install_status()}
        _JOB.clear()
        _JOB.update({'status': 'installing', 'progress': 0.0, 'started_at': time.time()})
    thread = threading.Thread(target=_install_worker, kwargs={'torch_variant': torch_variant}, daemon=True)
    thread.start()
    return {'success': True, 'started': True, **install_status()}
