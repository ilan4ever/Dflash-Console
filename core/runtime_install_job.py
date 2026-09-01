"""Shared helpers for on-demand runtime installs (vLLM, Transformers)."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Any, Callable

_PROGRESS_MARKER = re.compile(r'DFLASH_PROGRESS\s+(\d+(?:\.\d+)?)\s*(.*)$', re.I)
_PIP_MB = re.compile(r'(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*MB', re.I)
_PIP_PCT = re.compile(r'(?<![\d.])(\d{1,3})%(?!\d)')


def parse_install_line(line: str) -> tuple[float | None, str]:
    """Return (progress 0-99, message) from installer or pip output."""
    text = str(line or '').strip()
    if not text:
        return None, ''
    marked = _PROGRESS_MARKER.search(text)
    if marked:
        return min(99.0, float(marked.group(1))), (marked.group(2) or '').strip() or text
    mb = _PIP_MB.search(text)
    if mb:
        done, total = float(mb.group(1)), float(mb.group(2))
        if total > 0:
            pct = 30.0 + min(1.0, done / total) * 50.0
            return pct, f'Downloading packages ({done:.1f}/{total:.1f} MB)'
    pct_match = _PIP_PCT.search(text)
    if pct_match and ('download' in text.lower() or '/' in text or 'mb' in text.lower()):
        pct = 30.0 + (int(pct_match.group(1)) / 100.0) * 50.0
        return pct, text[:160]
    lower = text.lower()
    if 'creating' in lower and ('venv' in lower or 'environment' in lower):
        return 12.0, 'Creating Python environment'
    if 'upgrading pip' in lower or 'upgrade pip' in lower:
        return 18.0, 'Upgrading pip'
    if 'checking for a windows' in lower or 'native windows wheel' in lower:
        return 22.0, 'Checking for a Windows package'
    if 'installing pytorch' in lower:
        return 35.0, 'Downloading PyTorch'
    if 'freetoken' in lower:
        return 55.0, 'Installing FreeToken in WSL'
    if 'trying official' in lower or 'pip install vllm' in lower or 'downloading vllm' in lower:
        return 32.0, 'Downloading vLLM'
    if 'wsl' in lower and 'vllm' in lower and 'install' in lower:
        return 40.0, 'Installing vLLM in WSL'
    if 'installing collected' in lower:
        return 86.0, 'Installing packages'
    if 'dependency conflict' in lower or 'pip\'s dependency resolver' in lower:
        return 88.0, 'Resolving Python package dependencies'
    if 'successfully installed' in lower:
        # Pip prints this for every wheel (setuptools, numpy, …). Only treat
        # the final vLLM / runtime package as near-complete.
        if any(token in lower for token in ('vllm', 'freetoken', 'torch', 'transformers')):
            return 94.0, 'Finishing install'
        return 88.0, 'Installing Python packages'
    if 'found existing installation' in lower or 'requirement already satisfied' in lower:
        return 87.0, 'Resolving Python package dependencies'
    if 'checking vllm import' in lower:
        return 96.0, 'Verifying vLLM import in WSL'
    if 'vllm runtime installed' in lower:
        return 99.0, 'vLLM is ready'
    if 'no official windows wheel' in lower or 'switching to wsl' in lower:
        return 38.0, 'No Windows package — switching to WSL'
    return None, text[:200]


def job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    started = float(job.get('started_at') or 0.0)
    elapsed = (time.time() - started) if started else 0.0
    return {
        'progress': job.get('progress'),
        'message': str(job.get('message') or ''),
        'log_line': str(job.get('log_line') or ''),
        'elapsed_s': max(0.0, elapsed),
        'updated_at': job.get('updated_at'),
    }


def apply_job_progress(
    lock: threading.Lock,
    job: dict[str, Any],
    *,
    progress: float | None = None,
    message: str = '',
    log_line: str = '',
    status: str | None = None,
    error: str | None = None,
) -> None:
    with lock:
        if progress is not None:
            current = float(job.get('progress') or 0.0)
            job['progress'] = max(current, min(99.0, float(progress)))
        if message:
            job['message'] = message[:240]
        if log_line:
            job['log_line'] = log_line.strip()[:240]
        if status:
            job['status'] = status
        if error is not None:
            job['error'] = error
        job['updated_at'] = time.time()


def _iter_output_chunks(stream: Any):
    buf = b''
    while True:
        chunk = stream.read(256)
        if not chunk:
            if buf:
                yield buf.decode('utf-8', 'replace')
            break
        buf += chunk
        while True:
            newline = buf.find(b'\n')
            carriage = buf.find(b'\r')
            cuts = [index for index in (newline, carriage) if index >= 0]
            if not cuts:
                break
            index = min(cuts)
            yield buf[:index].decode('utf-8', 'replace')
            buf = buf[index + 1:]


def run_install_process(
    cmd: list[str],
    *,
    cwd: str,
    timeout: float,
    on_line: Callable[[str], None],
) -> int:
    """Run an installer and stream stdout/stderr line-by-line (including pip \\r updates)."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    started = time.time()
    try:
        for line in _iter_output_chunks(proc.stdout):
            on_line(line)
            if time.time() - started > timeout:
                proc.kill()
                break
        return int(proc.wait(timeout=60) or 0)
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass


def start_progress_heartbeat(
    lock: threading.Lock,
    job: dict[str, Any],
    *,
    started_at: float,
    stop: threading.Event,
) -> threading.Thread:
    """Keep the bar moving while pip is quiet so the UI never looks frozen at 5%."""

    def beat() -> None:
        while not stop.wait(2.0):
            elapsed = max(0.0, time.time() - started_at)
            current = float(job.get('progress') or 0.0)
            creep = min(88.0, 8.0 + elapsed / 12.0)
            if current >= 85.0:
                # Large WSL wheels can sit at "successfully installed" for minutes
                # while import verification runs — keep the bar moving slowly.
                creep = max(creep, min(98.5, current + 0.35))
            msg = ''
            if current >= 90.0 and creep > current:
                msg = 'Installing large packages in WSL — can take 10+ minutes'
            apply_job_progress(lock, job, progress=creep, message=msg)

    thread = threading.Thread(target=beat, daemon=True, name='runtime-install-heartbeat')
    thread.start()
    return thread
