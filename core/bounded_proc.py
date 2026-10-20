"""Subprocess calls that always return, even when a child ignores a kill.

Python's usual timeout kills the child and then waits forever for it to exit.
After a reboot, nvidia-smi can sit inside the NVIDIA driver and never exit, so
that wait freezes the Engines page and the GPU lights in the top bar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

_NVIDIA_LOCK = threading.Lock()
_NVIDIA_COOLDOWN_UNTIL = 0.0
_NVIDIA_LAST_OK: dict[tuple[str, ...], str] = {}
_POWERSHELL_EXE: str | None = None


def powershell_exe() -> str:
    """Absolute path to Windows PowerShell.

    The Console server is often started hidden by the desktop shell with a
    trimmed ``PATH`` that does not contain ``System32\\WindowsPowerShell\\v1.0``.
    A bare ``powershell`` lookup then fails to spawn, which silently removes
    CPU/RAM stats, per-process VRAM and model-path detection.
    """
    global _POWERSHELL_EXE
    if _POWERSHELL_EXE:
        return _POWERSHELL_EXE
    candidates: list[str] = []
    root = os.environ.get('SystemRoot') or r'C:\Windows'
    candidates.append(os.path.join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'))
    found = shutil.which('powershell')
    if found:
        candidates.append(found)
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            _POWERSHELL_EXE = candidate
            return candidate
    _POWERSHELL_EXE = 'powershell'
    return _POWERSHELL_EXE


def powershell_argv(script: str, *, hidden: bool = True) -> list[str]:
    """Command line for a one-shot PowerShell script."""
    argv = [powershell_exe(), '-NoProfile', '-NonInteractive']
    if hidden:
        argv += ['-WindowStyle', 'Hidden']
    argv += ['-Command', script]
    return argv


def _no_window_kwargs() -> dict[str, Any]:
    if sys.platform != 'win32':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    return {'startupinfo': startupinfo, 'creationflags': flags}


def run_bounded(argv: list[str], *, timeout: float = 3.0) -> tuple[int | None, str]:
    """Run a command and return ``(returncode, stdout)``.

    ``returncode`` is ``None`` when the command does not finish in time. The
    caller is never left waiting on a process that will not die.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None, ''
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=0.4)
        except subprocess.TimeoutExpired:
            return None, ''
        return None, ''
    text = (out or b'').decode('utf-8', errors='replace').strip()
    return proc.returncode, text


def run_nvidia_smi(args: list[str], *, timeout: float = 3.0) -> str:
    """One shared NVIDIA query for every screen that shows the GPU.

    Concurrent callers wait for the in-flight query instead of returning an
    empty answer (an empty GPU process list renders the Engines page blank).
    A failed query falls back to the last good answer.
    """
    global _NVIDIA_COOLDOWN_UNTIL
    key = tuple(args)
    now = time.time()
    if now < _NVIDIA_COOLDOWN_UNTIL:
        cached = _NVIDIA_LAST_OK.get(key, '')
        if cached:
            return cached
    acquired = _NVIDIA_LOCK.acquire(timeout=2.0)
    if not acquired:
        return _NVIDIA_LAST_OK.get(key, '')
    try:
        if time.time() < _NVIDIA_COOLDOWN_UNTIL:
            cached = _NVIDIA_LAST_OK.get(key, '')
            if cached:
                return cached
        code, text = run_bounded(['nvidia-smi', *args], timeout=timeout)
        if code is None:
            _NVIDIA_COOLDOWN_UNTIL = time.time() + 20.0
            return _NVIDIA_LAST_OK.get(key, '')
        if code != 0 or not text:
            return _NVIDIA_LAST_OK.get(key, '')
        _NVIDIA_LAST_OK[key] = text
        return text
    finally:
        _NVIDIA_LOCK.release()
