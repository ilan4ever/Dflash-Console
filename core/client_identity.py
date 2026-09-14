"""Identify which client loaded or requested a Console engine."""

from __future__ import annotations

import threading
from typing import Any

CLIENT_HEADER = 'x-dflash-client'
STRICT_MODEL_HEADER = 'x-dflash-strict-model'
LOAD_CONTEXT_HEADER = 'x-dflash-load-context'
MIN_LOAD_CONTEXT = 2048
MAX_LOAD_CONTEXT = 1_048_576
LABEL_CONSOLE_UI = 'DFlash Console'
LABEL_UNKNOWN_API = 'Unknown API client'

_BUILTIN_CLIENT_HINTS: tuple[tuple[str, str], ...] = (
    ('onevoice', 'OneVoice'),
    ('lm studio', 'LM Studio'),
    ('ollama', 'Ollama'),
    ('open-webui', 'Open WebUI'),
)


def _header_value(request: Any, name: str) -> str:
    if request is None:
        return ''
    headers = getattr(request, 'headers', None)
    if headers is None:
        return ''
    return str(headers.get(name) or '').strip()


def _console_ui_referer(referer: str) -> bool:
    ref = str(referer or '').strip().lower()
    if not ref:
        return False
    return ':8900/' in ref or ref.endswith(':8900') or '/static/' in ref


def resolve_client_label(request: Any | None = None) -> str:
    """Return a display label for the calling client.

    Priority:
      1. ``X-DFlash-Client`` request header (recommended for integrations)
      2. Known substrings in User-Agent / Referer
      3. Referer from the Console UI (legacy browser calls without the header)
      4. ``Unknown API client`` for unidentified API traffic
    """
    explicit = _header_value(request, CLIENT_HEADER) or _header_value(request, 'X-DFlash-Client')
    if explicit:
        return explicit[:120]

    ua = _header_value(request, 'user-agent').lower()
    referer = _header_value(request, 'referer').lower()
    for hint, label in _BUILTIN_CLIENT_HINTS:
        if hint in ua or hint in referer:
            return label

    if _console_ui_referer(referer):
        return LABEL_CONSOLE_UI

    return LABEL_UNKNOWN_API


def request_strict_model_match(request: Any | None = None) -> bool:
    """True when the client wants chat rejected if ``model`` != loaded checkpoint."""
    value = _header_value(request, STRICT_MODEL_HEADER) or _header_value(request, 'X-DFlash-Strict-Model')
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _normalize_load_context_value(raw: Any) -> int | None:
    if raw is None or raw == '':
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    if value < MIN_LOAD_CONTEXT or value > MAX_LOAD_CONTEXT:
        return None
    return value


def request_load_context_size(request: Any | None = None) -> int | None:
    """Minimum engine load context from ``X-DFlash-Load-Context`` (Harness and other integrators)."""
    raw = _header_value(request, LOAD_CONTEXT_HEADER) or _header_value(request, 'X-DFlash-Load-Context')
    return _normalize_load_context_value(raw)


def chat_body_load_context_size(body: Any) -> int | None:
    """``context_size`` on the chat JSON body (Runtime JSON shapes — override per chat)."""
    if not isinstance(body, dict):
        return None
    return _normalize_load_context_value(body.get('context_size'))


def display_loaded_by_label(raw: Any) -> str:
    """Normalize stored ``loaded_by`` for UI display."""
    text = str(raw or '').strip()
    if not text:
        return LABEL_UNKNOWN_API
    return text


_ACTIVE_CLIENT_LOCK = threading.Lock()
_ACTIVE_CLIENT_BY_SERVER: dict[str, str] = {}
_ACTIVE_CLIENT_REFCOUNT: dict[str, dict[str, int]] = {}


def set_active_client_label(server_id: str, label: str) -> bool:
    """Remember which client last activated an engine (inference or load).

    Returns True when the label changed (UI should refresh).
    """
    sid = str(server_id or '').strip()
    text = str(label or '').strip()
    if not sid or not text:
        return False
    with _ACTIVE_CLIENT_LOCK:
        if _ACTIVE_CLIENT_BY_SERVER.get(sid) == text:
            return False
        _ACTIVE_CLIENT_BY_SERVER[sid] = text
        return True


def get_active_client_label(server_id: str) -> str:
    sid = str(server_id or '').strip()
    if not sid:
        return ''
    with _ACTIVE_CLIENT_LOCK:
        return str(_ACTIVE_CLIENT_BY_SERVER.get(sid) or '').strip()


def clear_active_client_label(server_id: str) -> None:
    clear_active_clients(server_id)


def begin_active_client(server_id: str, label: str) -> bool:
    """Increment in-flight usage for a client label (parallel requests supported)."""
    sid = str(server_id or '').strip()
    text = str(label or '').strip()
    if not sid or not text:
        return False
    changed = False
    with _ACTIVE_CLIENT_LOCK:
        counts = _ACTIVE_CLIENT_REFCOUNT.setdefault(sid, {})
        prev = int(counts.get(text) or 0)
        counts[text] = prev + 1
        if prev <= 0:
            changed = True
        if _ACTIVE_CLIENT_BY_SERVER.get(sid) != text:
            _ACTIVE_CLIENT_BY_SERVER[sid] = text
            changed = True
    if changed:
        try:
            from core.runtime import invalidate_status_payload_cache

            invalidate_status_payload_cache()
        except Exception:
            pass
    return changed


def end_active_client(server_id: str, label: str) -> bool:
    """Decrement in-flight usage when a request finishes."""
    sid = str(server_id or '').strip()
    text = str(label or '').strip()
    if not sid or not text:
        return False
    changed = False
    with _ACTIVE_CLIENT_LOCK:
        counts = _ACTIVE_CLIENT_REFCOUNT.get(sid) or {}
        prev = int(counts.get(text) or 0)
        if prev <= 0:
            return False
        if prev == 1:
            counts.pop(text, None)
            if not counts:
                _ACTIVE_CLIENT_REFCOUNT.pop(sid, None)
            changed = True
        else:
            counts[text] = prev - 1
    if changed:
        try:
            from core.runtime import invalidate_status_payload_cache

            invalidate_status_payload_cache()
        except Exception:
            pass
    return changed


def list_active_clients(server_id: str) -> list[str]:
    """Return client labels with at least one in-flight request."""
    sid = str(server_id or '').strip()
    if not sid:
        return []
    with _ACTIVE_CLIENT_LOCK:
        counts = _ACTIVE_CLIENT_REFCOUNT.get(sid) or {}
        return sorted(label for label, count in counts.items() if int(count or 0) > 0)


def clear_active_clients(server_id: str) -> None:
    sid = str(server_id or '').strip()
    if not sid:
        return
    with _ACTIVE_CLIENT_LOCK:
        _ACTIVE_CLIENT_BY_SERVER.pop(sid, None)
        _ACTIVE_CLIENT_REFCOUNT.pop(sid, None)


def resolve_active_clients(server_id: str, stored_loaded_by: Any = None) -> list[str]:
    """Labels for engine cards: in-flight clients, else who loaded the checkpoint."""
    active = [display_loaded_by_label(label) for label in list_active_clients(server_id)]
    if active:
        return active
    stored = display_loaded_by_label(stored_loaded_by)
    return [stored] if stored else []


def resolve_engine_client_label(server_id: str, stored_loaded_by: Any = None) -> str:
    """Primary label for engine cards: in-flight, last touch, else who loaded."""
    active = list_active_clients(server_id)
    if active:
        return display_loaded_by_label(active[0])
    last = get_active_client_label(server_id)
    if last:
        return display_loaded_by_label(last)
    return display_loaded_by_label(stored_loaded_by)
