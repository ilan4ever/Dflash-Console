"""Rotating support journal and install/usage metadata for bug reports."""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import ROOT
from core.version import APP_VERSION

LOG_DIR = ROOT / 'logs'
JOURNAL_PATH = LOG_DIR / 'support-journal.log'
META_PATH = LOG_DIR / 'support-meta.json'

MAX_JOURNAL_LINES = 2500
MAX_JOURNAL_BYTES = 1_500_000
MAX_LOAD_HISTORY = 80

_LOCK = threading.Lock()
_SECRET_KEY_HINTS = ('token', 'password', 'secret', 'api_key', 'apikey', 'authorization')


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _read_meta_unlocked() -> dict[str, Any]:
    if not META_PATH.is_file():
        return {}
    try:
        raw = json.loads(META_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_meta_unlocked(meta: dict[str, Any]) -> None:
    _ensure_log_dir()
    META_PATH.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding='utf-8')


def ensure_support_meta(*, shell_version: str = '') -> dict[str, Any]:
    """Record first-run / first-server timestamps once."""
    with _LOCK:
        meta = _read_meta_unlocked()
        now = _iso_now()
        if not meta.get('first_run_at'):
            meta['first_run_at'] = now
            meta['app_version_first_seen'] = APP_VERSION
            meta['platform'] = platform.platform()
            meta['python_version'] = platform.python_version()
            if shell_version:
                meta['shell_version_first_seen'] = shell_version
        meta['last_boot_at'] = now
        meta['last_app_version'] = APP_VERSION
        if shell_version:
            meta['shell_version'] = shell_version
        meta.setdefault('model_load_history', [])
        meta.setdefault('session_count', 0)
        meta['session_count'] = int(meta.get('session_count') or 0) + 1
        _write_meta_unlocked(meta)
        return dict(meta)


def note_first_server_started(server_id: str = '') -> None:
    with _LOCK:
        meta = _read_meta_unlocked()
        if not meta.get('first_server_at'):
            meta['first_server_at'] = _iso_now()
            if server_id:
                meta['first_server_id'] = str(server_id)
            _write_meta_unlocked(meta)


def record_model_event(
    *,
    event: str,
    server_id: str = '',
    model_id: str = '',
    client: str = '',
    detail: str = '',
) -> None:
    row = {
        'at': _iso_now(),
        'event': str(event or '').strip() or 'load',
        'server_id': str(server_id or '').strip(),
        'model_id': str(model_id or '').strip(),
        'client': str(client or '').strip(),
        'detail': str(detail or '').strip()[:240],
    }
    with _LOCK:
        meta = _read_meta_unlocked()
        history = [item for item in (meta.get('model_load_history') or []) if isinstance(item, dict)]
        history.append(row)
        meta['model_load_history'] = history[-MAX_LOAD_HISTORY:]
        _write_meta_unlocked(meta)
    parts = [f'event={row["event"]}']
    if row['server_id']:
        parts.append(f'server={row["server_id"]}')
    if row['model_id']:
        parts.append(f'model={row["model_id"]}')
    if row['client']:
        parts.append(f'client={row["client"]}')
    if row['detail']:
        parts.append(f'detail={row["detail"]}')
    journal_event('load', ' | '.join(parts))


def _trim_journal_file() -> None:
    if not JOURNAL_PATH.is_file():
        return
    try:
        size = JOURNAL_PATH.stat().st_size
        if size <= MAX_JOURNAL_BYTES:
            lines = JOURNAL_PATH.read_text(encoding='utf-8', errors='replace').splitlines()
            if len(lines) <= MAX_JOURNAL_LINES:
                return
        else:
            lines = JOURNAL_PATH.read_text(encoding='utf-8', errors='replace').splitlines()
        keep = lines[-MAX_JOURNAL_LINES:]
        JOURNAL_PATH.write_text('\n'.join(keep) + ('\n' if keep else ''), encoding='utf-8')
    except OSError:
        return


def journal_event(category: str, message: str, **fields: Any) -> None:
    """Append one timestamped support journal line (bounded ring file)."""
    cat = str(category or 'info').strip().lower()[:32] or 'info'
    text = ' '.join(str(message or '').split())
    if not text and not fields:
        return
    extras = []
    for key, value in fields.items():
        if value is None:
            continue
        key_text = str(key).strip()
        if not key_text:
            continue
        val_text = ' '.join(str(value).split())
        if any(hint in key_text.lower() for hint in _SECRET_KEY_HINTS):
            val_text = '[redacted]'
        extras.append(f'{key_text}={val_text[:200]}')
    line = f'{_utc_now()} [{cat}] {text}'
    if extras:
        line = f'{line} | {" ".join(extras)}'
    with _LOCK:
        _ensure_log_dir()
        with JOURNAL_PATH.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
        _trim_journal_file()


def journal_error(message: str, **fields: Any) -> None:
    journal_event('error', message, **fields)


def read_journal_lines(*, tail: int = 300) -> list[str]:
    limit = max(1, min(int(tail or 300), MAX_JOURNAL_LINES))
    with _LOCK:
        if not JOURNAL_PATH.is_file():
            return []
        try:
            lines = JOURNAL_PATH.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            return []
    return lines[-limit:]


def get_support_meta() -> dict[str, Any]:
    with _LOCK:
        return dict(_read_meta_unlocked())


def redact_mapping(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return '[truncated]'
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(hint in key_text.lower() for hint in _SECRET_KEY_HINTS):
                out[key_text] = '[redacted]'
            else:
                out[key_text] = redact_mapping(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact_mapping(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + '…'
    return value
