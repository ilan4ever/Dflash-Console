"""Notice for people running the server when GitHub has a newer release."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any, Callable

from core.version import APP_VERSION

GITHUB_LATEST_API = 'https://api.github.com/repos/ilan4ever/Dflash-Console/releases/latest'
RELEASE_PAGE = 'https://github.com/ilan4ever/Dflash-Console/releases/latest'
_CACHE_SECONDS = 60 * 60
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {'at': 0.0, 'notice': None}


def version_key(value: str) -> tuple[int, ...]:
    text = str(value or '').strip()
    if text[:1] in {'v', 'V'}:
        text = text[1:]
    parts: list[int] = []
    for piece in text.split('.'):
        digits = ''
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer_release(latest: str, current: str) -> bool:
    new = version_key(latest)
    old = version_key(current)
    if not new or not old:
        return False
    return new > old


def _fetch_latest_version() -> str:
    request = urllib.request.Request(
        GITHUB_LATEST_API,
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': f'DFlash-Console/{APP_VERSION}',
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode('utf-8', errors='replace'))
    if not isinstance(payload, dict):
        return ''
    return str(payload.get('tag_name') or payload.get('name') or '').strip()


def release_notice(
    *,
    current: str = APP_VERSION,
    fetch_latest: Callable[[], str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Return whether GitHub's latest release is newer than this server."""
    now = time.time()
    if fetch_latest is None and not force:
        with _cache_lock:
            cached = _cache.get('notice')
            if isinstance(cached, dict) and (now - float(_cache.get('at') or 0.0)) < _CACHE_SECONDS:
                return dict(cached)

    latest = ''
    try:
        latest = (fetch_latest or _fetch_latest_version)()
    except Exception:
        latest = ''
    newer = is_newer_release(latest, current)
    shown = latest[1:] if latest[:1] in {'v', 'V'} else latest
    notice = {
        'success': True,
        'update_available': newer,
        'current_version': current,
        'latest_version': shown,
        'release_url': RELEASE_PAGE,
        'message': (
            f'A new release is available (v{shown}). Pull the latest code and restart the server.'
            if newer and shown
            else ''
        ),
    }
    if fetch_latest is None:
        with _cache_lock:
            _cache['at'] = time.time()
            _cache['notice'] = dict(notice)
    return notice
