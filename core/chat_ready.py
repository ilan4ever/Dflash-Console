"""Fast chat readiness checks — no GPU probes, log tails, or model stack builds."""

from __future__ import annotations

from typing import Any

from core.config import get_server, load_config
from core.engine_state import get_engine_state, note_engine_on
from core.runtime import tcp_port_open
from core.server_boot import ensure_managed_listen_port, listener_is_managed_engine


def _sync_engine_on(server: dict[str, Any], *, cfg: dict[str, Any], server_id: str) -> None:
    note_engine_on(server_id)
    entry = get_server(cfg, server_id)
    if isinstance(entry, dict):
        entry['engine_on'] = True
    server['engine_on'] = True


def ensure_engine_listener_for_chat(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start the engine listener for inbound chat when it is off or not listening.

    ``engine_off`` only affects Console boot restore. Gateway and JIT chat must
    not require a manual Engines toggle first.
    """
    from core.config import is_embedding_server
    from core.embedding_server import start_embedding_server
    from core.server_boot import start_router_listener

    config = cfg or load_config()
    server_id = str(server.get('id') or '').strip()
    if not server_id or server.get('enabled', True) is False:
        return {'success': False, 'reason': 'disabled'}

    host = str(server.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    port = int(server.get('port') or 0)
    if port <= 0:
        return {'success': False, 'reason': 'no_port'}

    port_info = ensure_managed_listen_port(server, cfg=config)
    if not port_info.get('success'):
        return {
            'success': False,
            'reason': 'start_failed',
            'error': str(port_info.get('error') or 'engine port is unavailable'),
            **port_info,
        }
    if port_info.get('reason') == 'rebound':
        fresh = get_server(config, server_id) or server
        host = str(fresh.get('host') or host).strip() or host
        port = int(fresh.get('port') or server.get('port') or 0)
        server['port'] = port
        server['api_url'] = str(fresh.get('api_url') or server.get('api_url') or '')

    if tcp_port_open(host, port) and listener_is_managed_engine(host, port):
        _sync_engine_on(server, cfg=config, server_id=server_id)
        return {'success': True, 'reason': 'already_listening', **port_info}

    _sync_engine_on(server, cfg=config, server_id=server_id)
    if is_embedding_server(server):
        result = start_embedding_server(server, cfg=config)
    else:
        result = start_router_listener(server, cfg=config)
    if result.get('success') and tcp_port_open(host, port):
        payload = {'success': True, 'reason': 'started', **result}
        if port_info.get('reason') == 'rebound':
            payload['previous_port'] = port_info.get('previous_port')
            payload['port'] = port
            payload['api_url'] = str(server.get('api_url') or result.get('api_url') or '')
        return payload
    return {
        'success': False,
        'reason': 'start_failed',
        'error': str(result.get('error') or 'could not start engine listener'),
        **result,
    }


def assess_server_chat_ready(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return whether chat may proceed for this engine profile."""
    config = cfg or load_config()
    server_id = str(server.get('id') or '').strip()
    label = str(server.get('label') or server_id or 'engine').strip()
    host = str(server.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    port = int(server.get('port') or 0)
    port_open = port > 0 and tcp_port_open(host, port) and listener_is_managed_engine(host, port)
    engine_on = get_engine_state(server_id, cfg=config).get('engine_on') is True

    # Saved engine_off is authoritative only when the listener is down. A live port
    # means the engine is running — reconcile stale config so UI and chat agree.
    if not engine_on and port_open:
        _sync_engine_on(server, cfg=config, server_id=server_id)
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
