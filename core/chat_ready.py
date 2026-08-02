"""Fast chat readiness checks — no GPU probes, log tails, or model stack builds."""

from __future__ import annotations

from typing import Any

from core.engine_state import get_engine_state
from core.runtime import tcp_port_open


def assess_server_chat_ready(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return whether chat may proceed for this engine profile."""
    from core.engine_state import note_engine_on

    server_id = str(server.get('id') or '').strip()
    label = str(server.get('label') or server_id or 'engine').strip()
    host = str(server.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    port = int(server.get('port') or 0)
    port_open = port > 0 and tcp_port_open(host, port)
    engine_on = get_engine_state(server_id, cfg=cfg).get('engine_on') is True

    # Saved engine_off is authoritative only when the listener is down. A live port
    # means the engine is running — reconcile stale config so UI and chat agree.
    if not engine_on and port_open:
        note_engine_on(server_id)
        engine_on = True

    if not engine_on:
        return {
            'ready': False,
            'ready_for_chat': False,
            'reason': 'engine_off',
            'engine_on': False,
            'label': label,
            'message': (
                f'The DFlash Console engine for {label} is turned off. '
                'Turn the engine on in the Console UI before sending chat.'
            ),
        }

    if port <= 0 or not port_open:
        return {
            'ready': False,
            'ready_for_chat': False,
            'reason': 'engine_stopped',
            'engine_on': True,
            'label': label,
            'message': (
                f'The DFlash Console engine for {label} is stopped. '
                'Turn the engine on in the Console UI.'
            ),
        }

    return {
        'ready': True,
        'ready_for_chat': True,
        'reason': 'ready',
        'engine_on': True,
        'label': label,
        'message': '',
    }


def chat_ready_http_error_detail(result: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-style error body for blocked chat requests."""
    reason = str(result.get('reason') or 'not_ready').strip()
    message = str(result.get('message') or 'Engine is not ready for chat.').strip()
    return {
        'error': {
            'message': message,
            'type': 'unavailable_error',
            'code': 503,
            'reason': reason,
        }
    }
