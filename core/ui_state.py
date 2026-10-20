"""Shared UI state - the bits both the Electron window and a browser must agree on.

Pins (Model library and Engines page) used to live only in ``localStorage``.
The Electron window and a browser tab are separate storage profiles, so a pin
made in one never appeared in the other. The shared lists now live in
``ui-state.json`` in the Console data root: every client reads and writes that
file through ``/api/ui-state`` and mirrors it into its own localStorage for an
instant paint.

Layout values (panel widths, table columns) stay in ``config.json -> ui_layout``
because they are per-device sizing; pins are user intent and must be shared.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterable as _AbcIterable
from pathlib import Path
from typing import Any

from core.config import ROOT

logger = logging.getLogger(__name__)

_STATE_PATH = ROOT / 'ui-state.json'
_PIN_SCOPES = ('models', 'engines')
_MAX_PINS = 4000
_MAX_KEY_CHARS = 400
_MAX_PREF_CHARS = 200_000
_MAX_PREF_NAME = 60

_lock = threading.Lock()


def _state_path() -> Path:
    return _STATE_PATH


def _clean_keys(values: Any) -> list[str]:
    """Normalize a pin list: strings only, trimmed, de-duplicated, stable order."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, _AbcIterable):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = str(raw or '').strip()
        if not key or len(key) > _MAX_KEY_CHARS:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= _MAX_PINS:
            break
    return out


def _pref_name(name: Any) -> str:
    value = str(name or '').strip()
    if not value or len(value) > _MAX_PREF_NAME:
        return ''
    if any(ch.isspace() for ch in value):
        return ''
    return value


def _pref_payload_ok(value: Any) -> bool:
    """Shared settings are small JSON values; refuse anything oversized."""
    try:
        return len(json.dumps(value, ensure_ascii=False)) <= _MAX_PREF_CHARS
    except (TypeError, ValueError):
        return False


def load_state() -> dict[str, Any]:
    """Read the shared state; always returns a complete, well-formed mapping."""
    path = _state_path()
    state: dict[str, Any] = {
        'pins': {scope: [] for scope in _PIN_SCOPES},
        'prefs': {},
        'rev': 0,
        'updated_at': 0.0,
    }
    if not path.is_file():
        return state
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('ui-state read failed: %s', exc)
        return state
    if not isinstance(payload, dict):
        return state
    pins = payload.get('pins') if isinstance(payload.get('pins'), dict) else {}
    for scope in _PIN_SCOPES:
        state['pins'][scope] = _clean_keys(pins.get(scope))
    prefs = payload.get('prefs') if isinstance(payload.get('prefs'), dict) else {}
    state['prefs'] = {
        str(name): value for name, value in prefs.items()
        if _pref_name(name) and _pref_payload_ok(value)
    }
    try:
        state['rev'] = int(payload.get('rev') or 0)
    except (TypeError, ValueError):
        state['rev'] = 0
    try:
        state['updated_at'] = float(payload.get('updated_at') or 0.0)
    except (TypeError, ValueError):
        state['updated_at'] = 0.0
    return state


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(
        json.dumps(
            {
                'version': 1,
                'rev': int(state.get('rev') or 0),
                'updated_at': float(state.get('updated_at') or 0.0),
                'pins': {
                    scope: _clean_keys((state.get('pins') or {}).get(scope))
                    for scope in _PIN_SCOPES
                },
                'prefs': {
                    str(name): value
                    for name, value in (state.get('prefs') or {}).items()
                    if _pref_name(name) and _pref_payload_ok(value)
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    os.replace(tmp, path)


def get_state() -> dict[str, Any]:
    with _lock:
        return load_state()


def set_pins(scope: str, keys: Any, *, client: str = '') -> dict[str, Any]:
    """Replace one pin list. Returns the whole state so callers can re-render."""
    name = str(scope or '').strip().lower()
    if name not in _PIN_SCOPES:
        return {'success': False, 'error': f'unknown pin scope: {scope}'}
    cleaned = _clean_keys(keys)
    with _lock:
        state = load_state()
        previous = list(state['pins'].get(name) or [])
        # Pins are a set: order is not meaningful, so a re-ordered list is not
        # a change and must not bump the revision (that re-renders clients).
        changed = set(previous) != set(cleaned)
        if changed:
            state['pins'][name] = cleaned
            state['rev'] = int(state.get('rev') or 0) + 1
            state['updated_at'] = time.time()
            _save_state(state)
    return {
        'success': True,
        'scope': name,
        'changed': changed,
        'client': str(client or ''),
        **state,
    }


def toggle_pin(scope: str, key: str, *, client: str = '') -> dict[str, Any]:
    name = str(scope or '').strip().lower()
    if name not in _PIN_SCOPES:
        return {'success': False, 'error': f'unknown pin scope: {scope}'}
    ident = str(key or '').strip()
    if not ident:
        return {'success': False, 'error': 'pin key is required'}
    with _lock:
        state = load_state()
        current = list(state['pins'].get(name) or [])
        if ident in current:
            current = [item for item in current if item != ident]
            pinned = False
        else:
            current.append(ident)
            pinned = True
        state['pins'][name] = current
        state['rev'] = int(state.get('rev') or 0) + 1
        state['updated_at'] = time.time()
        _save_state(state)
    return {
        'success': True,
        'scope': name,
        'key': ident,
        'pinned': pinned,
        'client': str(client or ''),
        **state,
    }


def set_pref(name: str, value: Any, *, client: str = '') -> dict[str, Any]:
    """Replace one shared setting (JSON value). ``None`` removes it."""
    key = _pref_name(name)
    if not key:
        return {'success': False, 'error': 'pref name is required'}
    if value is not None and not _pref_payload_ok(value):
        return {'success': False, 'error': 'pref value is too large or not JSON'}
    with _lock:
        state = load_state()
        prefs = dict(state.get('prefs') or {})
        had = key in prefs
        if value is None:
            prefs.pop(key, None)
            changed = had
        else:
            changed = not had or prefs.get(key) != value
            prefs[key] = value
        state['prefs'] = prefs
        if changed:
            state['rev'] = int(state.get('rev') or 0) + 1
            state['updated_at'] = time.time()
            _save_state(state)
    return {
        'success': True,
        'name': key,
        'changed': changed,
        'client': str(client or ''),
        **state,
    }
