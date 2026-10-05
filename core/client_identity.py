"""Identify which client loaded or requested a Console engine."""

from __future__ import annotations

import re
import threading
from typing import Any

CLIENT_HEADER = 'x-dflash-client'
STRICT_MODEL_HEADER = 'x-dflash-strict-model'
LOAD_CONTEXT_HEADER = 'x-dflash-load-context'
COMPONENT_HEADER = 'x-dflash-component'
ROLE_HEADER = 'x-dflash-role'
MIN_LOAD_CONTEXT = 2048
MAX_LOAD_CONTEXT = 1_048_576
LABEL_CONSOLE_UI = 'DFlash Console'
LABEL_UNKNOWN_API = 'Unknown API client'

COMPONENT_LABELS: dict[str, str] = {
    'onevoice.main_brain': 'OneVoice Main Brain',
    'onevoice.app': 'OneVoice App/API',
    'onevoice.mediator': 'OneVoice Mediator',
    'onevoice.ocr': 'OneVoice OCR',
    'onevoice.translation': 'OneVoice Translation',
    'onevoice.summarizer': 'OneVoice Summarizer',
    'onevoice.reflector': 'OneVoice Reflector',
    'onevoice.file_organizer': 'OneVoice File Organizer',
    'onevoice.session_rename': 'OneVoice Session Rename',
    'onevoice.memory_saving': 'OneVoice Memory Saving',
    'onevoice.memory_facts_updater': 'OneVoice Facts Updater',
    'onevoice.memory_deep_observer': 'OneVoice Deep Observer',
    'onevoice.memory_synthesizer': 'OneVoice Memory Synthesizer',
    'onevoice.memory_user_profile': 'OneVoice User Profile',
    'onevoice.memory_nightly_consolidator': 'OneVoice Nightly Consolidator',
    'onevoice.free_speak': 'OneVoice Fluent / Free Speak',
    'onevoice.developer': 'OneVoice Developer',
    'onevoice.stt': 'OneVoice Speech-to-text',
    'onevoice.hermes_stt': 'OneVoice Hermes STT',
    'onevoice.speak_stt': 'OneVoice Speak STT',
    'onevoice.f5_tts': 'OneVoice F5-TTS',
    'onevoice.tts': 'OneVoice Text-to-speech',
    # Keep the old keys readable for persisted status snapshots and older
    # clients, while new requests use the structured OneVoice keys above.
    'hermes.stt': 'OneVoice Hermes STT',
    'speak.stt': 'OneVoice Speak STT',
    'f5.tts': 'OneVoice F5-TTS',
}

_ROLE_COMPONENTS: dict[str, tuple[str, str]] = {
    'main': ('onevoice.main_brain', COMPONENT_LABELS['onevoice.main_brain']),
    'mediator': ('onevoice.mediator', COMPONENT_LABELS['onevoice.mediator']),
    'image_ocr': ('onevoice.ocr', COMPONENT_LABELS['onevoice.ocr']),
    'translation': ('onevoice.translation', COMPONENT_LABELS['onevoice.translation']),
    'free_speak': ('onevoice.free_speak', COMPONENT_LABELS['onevoice.free_speak']),
    'summarizer': ('onevoice.summarizer', COMPONENT_LABELS['onevoice.summarizer']),
    'developer': ('onevoice.developer', COMPONENT_LABELS['onevoice.developer']),
    'file_organizer': ('onevoice.file_organizer', COMPONENT_LABELS['onevoice.file_organizer']),
    'session_rename': ('onevoice.session_rename', COMPONENT_LABELS['onevoice.session_rename']),
    'memory_saving': ('onevoice.memory_saving', COMPONENT_LABELS['onevoice.memory_saving']),
    'memory_facts_updater': ('onevoice.memory_facts_updater', COMPONENT_LABELS['onevoice.memory_facts_updater']),
    'memory_deep_observer': ('onevoice.memory_deep_observer', COMPONENT_LABELS['onevoice.memory_deep_observer']),
    'memory_synthesizer': ('onevoice.memory_synthesizer', COMPONENT_LABELS['onevoice.memory_synthesizer']),
    'memory_user_profile': ('onevoice.memory_user_profile', COMPONENT_LABELS['onevoice.memory_user_profile']),
    'memory_nightly_consolidator': ('onevoice.memory_nightly_consolidator', COMPONENT_LABELS['onevoice.memory_nightly_consolidator']),
    'memory_shared_harvest': ('onevoice.memory_saving', COMPONENT_LABELS['onevoice.memory_saving']),
    'stt': ('onevoice.hermes_stt', COMPONENT_LABELS['onevoice.hermes_stt']),
    'hermes_stt': ('onevoice.hermes_stt', COMPONENT_LABELS['onevoice.hermes_stt']),
    'speak_stt': ('onevoice.speak_stt', COMPONENT_LABELS['onevoice.speak_stt']),
    'f5_tts': ('onevoice.f5_tts', COMPONENT_LABELS['onevoice.f5_tts']),
}

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


def _normalize_component_key(raw: Any) -> str:
    text = str(raw or '').strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'main': 'onevoice.main_brain',
        'main_brain': 'onevoice.main_brain',
        'onevoice_main_brain': 'onevoice.main_brain',
        'onevoice.main_brain': 'onevoice.main_brain',
        'mediator': 'onevoice.mediator',
        'onevoice_mediator': 'onevoice.mediator',
        'ocr': 'onevoice.ocr',
        'image_ocr': 'onevoice.ocr',
        'onevoice_ocr': 'onevoice.ocr',
        'translation': 'onevoice.translation',
        'hermes_stt': 'onevoice.hermes_stt',
        'speech_hermes': 'onevoice.hermes_stt',
        'onevoice_hermes_stt': 'onevoice.hermes_stt',
        'speak_stt': 'onevoice.speak_stt',
        'onevoice_speak_stt': 'onevoice.speak_stt',
        'f5_tts': 'onevoice.f5_tts',
        'onevoice_f5_tts': 'onevoice.f5_tts',
        'f5-tts': 'f5.tts',
    }
    if text in aliases:
        return aliases[text]
    if text.startswith('onevoice.') and len(text) > len('onevoice.'):
        return text
    if text in COMPONENT_LABELS:
        return text
    if not text:
        return ''
    safe = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    return f'external.{safe}' if safe else ''


def component_identity(
    *,
    component: Any = '',
    role: Any = '',
    client: Any = '',
) -> dict[str, str]:
    """Return stable component fields for status/API attribution."""
    role_text = str(role or '').strip().lower().replace('-', '_').replace(' ', '_')
    component_key = _normalize_component_key(component)
    if not component_key and role_text in _ROLE_COMPONENTS:
        component_key = _ROLE_COMPONENTS[role_text][0]
    if component_key in COMPONENT_LABELS:
        label = COMPONENT_LABELS[component_key]
    elif component_key:
        label = str(component or '').strip() or component_key
    elif str(client or '').strip().lower().startswith('onevoice'):
        component_key = 'onevoice.app'
        label = 'OneVoice AI'
    else:
        label = ''
    return {
        'component_key': component_key,
        'component_label': label,
        'component_role': role_text,
    }


def request_component_identity(request: Any | None = None) -> dict[str, str]:
    return component_identity(
        component=_header_value(request, COMPONENT_HEADER) or _header_value(request, 'X-DFlash-Component'),
        role=_header_value(request, ROLE_HEADER) or _header_value(request, 'X-DFlash-Role'),
        client=resolve_client_label(request),
    )


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
_ACTIVE_COMPONENT_BY_SERVER: dict[str, dict[str, str]] = {}
_ACTIVE_COMPONENT_REFCOUNT: dict[str, dict[str, dict[str, Any]]] = {}


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


def set_active_component(
    server_id: str,
    *,
    component_key: Any = '',
    component_label: Any = '',
    component_role: Any = '',
) -> bool:
    sid = str(server_id or '').strip()
    identity = component_identity(component=component_key, role=component_role)
    key = str(identity.get('component_key') or '').strip()
    if not sid or not key:
        return False
    row = {
        'component_key': key,
        'component_label': str(component_label or identity.get('component_label') or key).strip(),
        'component_role': str(component_role or identity.get('component_role') or '').strip(),
    }
    with _ACTIVE_CLIENT_LOCK:
        changed = _ACTIVE_COMPONENT_BY_SERVER.get(sid) != row
        _ACTIVE_COMPONENT_BY_SERVER[sid] = row
    return changed


def begin_active_component(
    server_id: str,
    *,
    component_key: Any = '',
    component_label: Any = '',
    component_role: Any = '',
) -> bool:
    sid = str(server_id or '').strip()
    identity = component_identity(component=component_key, role=component_role)
    key = str(identity.get('component_key') or '').strip()
    if not sid or not key:
        return False
    label = str(component_label or identity.get('component_label') or key).strip()
    role = str(component_role or identity.get('component_role') or '').strip()
    changed = False
    with _ACTIVE_CLIENT_LOCK:
        rows = _ACTIVE_COMPONENT_REFCOUNT.setdefault(sid, {})
        current = rows.get(key) or {
            'component_key': key,
            'component_label': label,
            'component_role': role,
            'count': 0,
        }
        current['count'] = int(current.get('count') or 0) + 1
        current['component_label'] = label
        current['component_role'] = role
        rows[key] = current
        next_row = {
            'component_key': key,
            'component_label': label,
            'component_role': role,
        }
        if _ACTIVE_COMPONENT_BY_SERVER.get(sid) != next_row:
            _ACTIVE_COMPONENT_BY_SERVER[sid] = next_row
            changed = True
    return changed


def end_active_component(server_id: str, *, component_key: Any = '') -> bool:
    sid = str(server_id or '').strip()
    key = _normalize_component_key(component_key)
    if not sid or not key:
        return False
    changed = False
    with _ACTIVE_CLIENT_LOCK:
        rows = _ACTIVE_COMPONENT_REFCOUNT.get(sid) or {}
        current = rows.get(key)
        if not isinstance(current, dict) or int(current.get('count') or 0) <= 0:
            return False
        current['count'] = int(current.get('count') or 0) - 1
        if current['count'] <= 0:
            rows.pop(key, None)
            if not rows:
                _ACTIVE_COMPONENT_REFCOUNT.pop(sid, None)
            changed = True
    return changed


def list_active_components(server_id: str) -> list[dict[str, str]]:
    sid = str(server_id or '').strip()
    if not sid:
        return []
    with _ACTIVE_CLIENT_LOCK:
        rows = _ACTIVE_COMPONENT_REFCOUNT.get(sid) or {}
        return [
            {
                'component_key': str(row.get('component_key') or key),
                'component_label': str(row.get('component_label') or key),
                'component_role': str(row.get('component_role') or ''),
            }
            for key, row in sorted(rows.items())
            if isinstance(row, dict) and int(row.get('count') or 0) > 0
        ]


def resolve_active_components(
    server_id: str,
    *,
    stored_component_key: Any = '',
    stored_component_label: Any = '',
    stored_component_role: Any = '',
) -> list[dict[str, str]]:
    active = list_active_components(server_id)
    if active:
        return active
    sid = str(server_id or '').strip()
    with _ACTIVE_CLIENT_LOCK:
        last = dict(_ACTIVE_COMPONENT_BY_SERVER.get(sid) or {})
    if last.get('component_key'):
        return [{
            'component_key': str(last.get('component_key') or ''),
            'component_label': str(last.get('component_label') or ''),
            'component_role': str(last.get('component_role') or ''),
        }]
    fallback = component_identity(component=stored_component_key, role=stored_component_role)
    key = str(fallback.get('component_key') or '').strip()
    if key:
        return [{
            'component_key': key,
            'component_label': str(stored_component_label or fallback.get('component_label') or key),
            'component_role': str(stored_component_role or fallback.get('component_role') or ''),
        }]
    return []


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
        _ACTIVE_COMPONENT_BY_SERVER.pop(sid, None)
        _ACTIVE_COMPONENT_REFCOUNT.pop(sid, None)


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
