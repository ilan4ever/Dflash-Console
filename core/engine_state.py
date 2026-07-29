"""Persist engine on/off in config.json. Checkpoints load only via UI/API — never on Console boot."""

from __future__ import annotations

from typing import Any

from core.config import get_server, list_servers, load_config, normalize_server, update_server_runtime
from core.runtime import probe_models, tcp_port_open, unload_model


def get_engine_state(server_id: str, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    entry = get_server(config, server_id) or {}
    return {'engine_on': entry.get('engine_on') is True}


def note_user_stopped(server_id: str) -> dict[str, Any]:
    return update_server_runtime(server_id, engine_on=False)


def note_engine_on(server_id: str) -> dict[str, Any]:
    return update_server_runtime(server_id, engine_on=True)


def note_engine_idle(server_id: str) -> dict[str, Any]:
    return note_engine_on(server_id)


def note_engine_loaded(server_id: str) -> dict[str, Any]:
    """Runtime load only — config remembers engine on, not which checkpoint is in VRAM."""
    return note_engine_on(server_id)


def release_gpu_checkpoints(server: dict[str, Any]) -> dict[str, Any]:
    """Unload any checkpoints from GPU on a running engine."""
    entry = normalize_server(server)
    host = str(entry.get('host') or '127.0.0.1')
    port = int(entry.get('port') or 0)
    api_url = str(entry.get('api_url') or '')
    if port <= 0 or not tcp_port_open(host, port) or not api_url:
        return {'success': True, 'unloaded': False}

    unloaded: list[str] = []
    errors: list[str] = []
    loaded = probe_models(api_url)
    for model_id in loaded:
        mid = str(model_id or '').strip()
        if not mid:
            continue
        result = unload_model(api_url=api_url, model_id=mid)
        if result.get('success'):
            unloaded.append(mid)
        elif result.get('error'):
            errors.append(str(result['error']))

    return {
        'success': not errors or bool(unloaded),
        'unloaded': bool(unloaded),
        'models': unloaded,
        'errors': errors or None,
    }


def release_all_gpu_checkpoints(*, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = cfg or load_config()
    results: list[dict[str, Any]] = []
    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        if port <= 0 or not tcp_port_open(host, port):
            continue
        row = release_gpu_checkpoints(server)
        results.append({'server_id': server['id'], **row})
    return results


def restore_engines(*, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """On Console boot: restore engine listen state only; always clear GPU checkpoints first."""
    from core.runtime import stop_server
    from core.server_boot import adopt_running_engine, start_router_listener

    config = cfg or load_config()
    results: list[dict[str, Any]] = []

    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        server_id = str(server['id'])
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        if port <= 0:
            continue

        runtime = get_engine_state(server_id, cfg=config)
        port_open = tcp_port_open(host, port)

        if not runtime.get('engine_on'):
            if port_open:
                stop_server(port=port, host=host, api_url=str(server.get('api_url') or ''))
                results.append({'server_id': server_id, 'action': 'stopped_saved_off'})
            else:
                results.append({'server_id': server_id, 'action': 'skipped_engine_off'})
            continue

        if port_open:
            adopt = adopt_running_engine(server, cfg=config)
            released = release_gpu_checkpoints(server)
            note_engine_idle(server_id)
            results.append({
                'server_id': server_id,
                'action': 'adopted_idle',
                'released': released,
                **adopt,
            })
            continue

        result = start_router_listener(server, cfg=config)
        if result.get('success'):
            note_engine_idle(server_id)
        results.append({'server_id': server_id, 'action': 'restore_idle', **result})

    return results
