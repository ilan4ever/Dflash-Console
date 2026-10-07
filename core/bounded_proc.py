"""Subprocess calls that always return, even when a child ignores a kill.

Python's usual timeout kills the child and then waits forever for it to exit.
After a reboot, nvidia-smi can sit inside the NVIDIA driver and never exit, so
that wait freezes the Engines page and the GPU lights in the top bar.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

_NVIDIA_LOCK = threading.Lock()
_NVIDIA_COOLDOWN_UNTIL = 0.0
_NVIDIA_LAST_OK: dict[tuple[str, ...], str] = {}


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

    A stuck query starts a short cooldown. Later callers get the last good
    answer instead of starting another check that would freeze too.
    """
    global _NVIDIA_COOLDOWN_UNTIL
    key = tuple(args)
    now = time.time()
    if now < _NVIDIA_COOLDOWN_UNTIL:
        return _NVIDIA_LAST_OK.get(key, '')
    if not _NVIDIA_LOCK.acquire(blocking=False):
        return _NVIDIA_LAST_OK.get(key, '')
    try:
        if time.time() < _NVIDIA_COOLDOWN_UNTIL:
            return _NVIDIA_LAST_OK.get(key, '')
        code, text = run_bounded(['nvidia-smi', *args], timeout=timeout)
        if code is None:
            _NVIDIA_COOLDOWN_UNTIL = time.time() + 20.0
            return _NVIDIA_LAST_OK.get(key, '')
        if code != 0 or not text:
            return ''
        _NVIDIA_LAST_OK[key] = text
        return text
    finally:
        _NVIDIA_LOCK.release()
