"""In-memory and on-disk log of console API requests."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / 'logs'
ACCESS_LOG_PATH = LOG_DIR / 'api-access.log'

_MAX_ENTRIES = 1000
_lock = threading.Lock()
_entries: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)

_ERROR_HINTS = ('error', 'exception', 'traceback', 'failed', 'critical', ' 4', ' 5')


def record_api_call(
    *,
    method: str,
    path: str,
    query: str = '',
    status: int = 0,
    duration_ms: float = 0.0,
    client: str = '',
    error: str = '',
) -> dict[str, Any]:
    row = {
        'at': time.time(),
        'method': str(method or '').upper(),
        'path': str(path or ''),
        'query': str(query or ''),
        'status': int(status or 0),
        'duration_ms': round(float(duration_ms or 0.0), 2),
        'client': str(client or ''),
        'error': str(error or '').strip(),
        'level': 'error' if error or status >= 400 else 'info',
    }
    with _lock:
        _entries.append(row)
    _append_disk(row)
    return row


def list_api_calls(*, tail: int = 200, errors_only: bool = False) -> list[dict[str, Any]]:
    limit = max(1, min(int(tail or 200), _MAX_ENTRIES))
    with _lock:
        rows = list(_entries)
    if errors_only:
        rows = [row for row in rows if row.get('level') == 'error' or int(row.get('status') or 0) >= 400]
    return rows[-limit:]


def _append_disk(row: dict[str, Any]) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(row.get('at') or time.time())))
        status = int(row.get('status') or 0)
        query = str(row.get('query') or '')
        suffix = f'?{query}' if query else ''
        error = str(row.get('error') or '').strip()
        line = (
            f'[{stamp}] {row.get("method")} {row.get("path")}{suffix} '
            f'-> {status} ({row.get("duration_ms")} ms)'
        )
        if error:
            line += f' ERROR: {error}'
        with ACCESS_LOG_PATH.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        return


def read_access_log_file(*, tail: int = 200) -> dict[str, Any]:
    limit = max(1, min(int(tail or 200), 5000))
    if not ACCESS_LOG_PATH.is_file():
        return {'path': str(ACCESS_LOG_PATH), 'exists': False, 'lines': [], 'total_lines': 0}
    try:
        lines = ACCESS_LOG_PATH.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return {'path': str(ACCESS_LOG_PATH), 'exists': True, 'lines': [], 'total_lines': 0}
    return {
        'path': str(ACCESS_LOG_PATH),
        'exists': True,
        'total_lines': len(lines),
        'lines': lines[-limit:],
    }


def is_error_line(line: str) -> bool:
    text = str(line or '').strip().lower()
    if not text:
        return False
    return any(hint in text for hint in _ERROR_HINTS)
