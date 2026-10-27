"""Installed and latest versions for the optionally installed engine modules.

The Console installs vLLM, Transformers and FreeToken on demand.  Those three
are normal Python packages, so PyPI is the source of truth for their latest
version.  Everything here is cached: the Components panel and the background
updater must never launch a WSL import on every status poll.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from core.config import ROOT
from core.version import APP_VERSION

# runtime_id -> Python distribution name on PyPI.
PYPI_PACKAGES: dict[str, str] = {
    'vllm': 'vllm',
    'transformers': 'transformers',
    'freetoken': 'freetoken',
}

_VERSION_CACHE_TTL = 1800.0
_VERSION_ERROR_TTL = 300.0
_VERSION_TEXT_RE = re.compile(r'(\d+\.\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?)')
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_AT = 0.0
_LAST_ERROR = ''
_STATE_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_REFRESH_PENDING = False
_REFRESH_THREAD: threading.Thread | None = None


def _manifest(runtime_id: str) -> dict[str, Any]:
    path = ROOT / 'runtimes' / runtime_id / 'manifest.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _run_text(cmd: list[str], *, timeout: float = 30.0) -> str:
    """Run a version probe without opening a console window or hanging Web UI."""
    kwargs: dict[str, Any] = {
        'capture_output': True,
        'text': True,
        'encoding': 'utf-8',
        'errors': 'replace',
        'timeout': timeout,
        'check': False,
    }
    if _CREATE_NO_WINDOW:
        kwargs['creationflags'] = _CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    return f'{result.stdout or ""}\n{result.stderr or ""}'.strip()


def _clean_version(text: str) -> str:
    """Extract the last version-looking token from command output."""
    matches = _VERSION_TEXT_RE.findall(str(text or ''))
    if not matches:
        return ''
    return matches[-1].strip()


def _python_installed_version(path: str, package: str) -> str:
    target = Path(str(path or '')).expanduser()
    if not target.is_file():
        return ''
    code = f'import {package}; print({package}.__version__)'
    return _clean_version(_run_text([str(target), '-c', code], timeout=45.0))


def _wsl_python_installed_version(distro: str, python: str, package: str) -> str:
    if not distro or not python:
        return ''
    code = f'import {package}; print({package}.__version__)'
    return _clean_version(_run_text(
        ['wsl', '-d', str(distro), '--', str(python), '-c', code],
        timeout=45.0,
    ))


def _installed_version(runtime_id: str, manifest: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return (version, error) for an installed on-demand engine."""
    manifest = manifest if isinstance(manifest, dict) else _manifest(runtime_id)
    if not manifest:
        return '', 'not installed'

    recorded = str(
        manifest.get('package_version')
        or manifest.get('ft_version')
        or manifest.get('vllm_version')
        or manifest.get('transformers_version')
        or ''
    ).strip()
    if recorded:
        return _clean_version(recorded), ''

    try:
        if runtime_id == 'freetoken':
            distro = str(manifest.get('wsl_distro') or '').strip()
            ft = str(manifest.get('wsl_ft') or '').strip()
            if not distro or not ft:
                return '', 'FreeToken WSL manifest is incomplete'
            return _clean_version(_run_text(
                ['wsl', '-d', distro, '--', ft, '--version'],
                timeout=20.0,
            )), ''
        if runtime_id == 'vllm':
            backend = str(manifest.get('backend') or '').strip().lower()
            if backend == 'wsl':
                return _wsl_python_installed_version(
                    str(manifest.get('wsl_distro') or ''),
                    str(manifest.get('wsl_python') or ''),
                    'vllm',
                ), ''
            return _python_installed_version(str(manifest.get('python') or ''), 'vllm'), ''
        if runtime_id == 'transformers':
            return _python_installed_version(str(manifest.get('python') or ''), 'transformers'), ''
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return '', str(exc)
    return '', 'unknown runtime'


def _pypi_latest(package: str) -> str:
    url = f'https://pypi.org/pypi/{package}/json'
    request = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': f'DFlash-Console/{APP_VERSION}',
        },
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        payload = json.loads(response.read().decode('utf-8', errors='replace'))
    info = payload.get('info') if isinstance(payload, dict) else {}
    version = str((info or {}).get('version') or '').strip()
    if not version:
        raise ValueError(f'PyPI returned no version for {package}')
    return version


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r'\d+', str(value or ''))]
    return tuple(numbers[:5])


def is_newer(latest: str, installed: str) -> bool:
    """True when latest is a numerically newer release than installed."""
    left = _version_tuple(latest)
    right = _version_tuple(installed)
    return bool(left and right and left > right)


def _copy_cache() -> dict[str, dict[str, Any]]:
    with _STATE_LOCK:
        return {runtime_id: dict(row) for runtime_id, row in _CACHE.items()}


def version_snapshot() -> dict[str, Any]:
    """Return cached versions without doing any slow probe."""
    with _STATE_LOCK:
        return {
            'versions': {runtime_id: dict(row) for runtime_id, row in _CACHE.items()},
            'checked_at': _CACHE_AT or None,
            'pending': _REFRESH_PENDING,
            'error': _LAST_ERROR,
            'stale': (time.time() - _CACHE_AT) > _VERSION_CACHE_TTL if _CACHE_AT else True,
        }


def refresh_versions(*, force: bool = False) -> dict[str, Any]:
    """Probe PyPI and every installed on-demand engine once, with caching."""
    global _CACHE, _CACHE_AT, _LAST_ERROR

    now = time.time()
    with _STATE_LOCK:
        if not force and _CACHE_AT and (now - _CACHE_AT) < _VERSION_CACHE_TTL:
            return version_snapshot()
        previous = {runtime_id: dict(row) for runtime_id, row in _CACHE.items()}

    with _REFRESH_LOCK:
        # Another request may have finished while this one waited for the lock.
        with _STATE_LOCK:
            if not force and _CACHE_AT and (time.time() - _CACHE_AT) < _VERSION_CACHE_TTL:
                return version_snapshot()
            previous = {runtime_id: dict(row) for runtime_id, row in _CACHE.items()}

        updated: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for runtime_id, package in PYPI_PACKAGES.items():
            row = dict(previous.get(runtime_id) or {})
            manifest = _manifest(runtime_id)
            latest = str(row.get('latest_version') or '').strip()
            latest_error = ''
            try:
                latest = _pypi_latest(package)
            except Exception as exc:
                latest_error = f'PyPI check failed: {exc}'
                errors.append(f'{package}: {exc}')

            installed = str(row.get('installed_version') or '').strip()
            installed_error = ''
            if manifest:
                try:
                    installed, installed_error = _installed_version(runtime_id, manifest)
                except Exception as exc:
                    installed_error = str(exc)
            else:
                installed_error = 'not installed'

            row.update({
                'runtime_id': runtime_id,
                'package': package,
                'installed_version': installed,
                'latest_version': latest,
                'version_update_available': is_newer(latest, installed),
                'latest_error': latest_error,
                'installed_error': installed_error,
            })
            updated[runtime_id] = row

        with _STATE_LOCK:
            _CACHE = updated
            _CACHE_AT = time.time()
            _LAST_ERROR = '; '.join(errors[:3])
            snapshot = {
                'versions': {runtime_id: dict(row) for runtime_id, row in _CACHE.items()},
                'checked_at': _CACHE_AT,
                'pending': _REFRESH_PENDING,
                'error': _LAST_ERROR,
                'stale': False,
            }
        return snapshot


def _refresh_worker(*, force: bool) -> None:
    global _REFRESH_PENDING
    try:
        refresh_versions(force=force)
    except Exception:
        pass
    finally:
        with _STATE_LOCK:
            _REFRESH_PENDING = False


def start_refresh_if_stale(*, force: bool = False) -> bool:
    """Start a background probe and return whether a job was started."""
    global _REFRESH_PENDING, _REFRESH_THREAD
    with _STATE_LOCK:
        if _REFRESH_PENDING:
            return False
        fresh = bool(_CACHE_AT) and (time.time() - _CACHE_AT) < _VERSION_CACHE_TTL
        if fresh and not force:
            return False
        _REFRESH_PENDING = True
    thread = threading.Thread(
        target=_refresh_worker,
        kwargs={'force': force},
        daemon=True,
        name='component-version-check',
    )
    _REFRESH_THREAD = thread
    thread.start()
    return True


def invalidate(runtime_id: str | None = None) -> None:
    """Drop cached versions after an install so the next check sees the new one."""
    global _CACHE_AT
    with _STATE_LOCK:
        if runtime_id:
            _CACHE.pop(str(runtime_id), None)
        else:
            _CACHE.clear()
        _CACHE_AT = 0.0


def bundled_version() -> str:
    return APP_VERSION
