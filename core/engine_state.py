"""Persist engine on/off in config.json. Checkpoints load only via UI/API — never on Console boot."""

from __future__ import annotations

from typing import Any

from core.config import (
    get_server,
    is_embedding_server,
    list_servers,
    load_config,
    normalize_server,
    update_server_runtime,
)
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


def note_engine_loaded(server_id: str, *, loaded_by: str | None = None) -> dict[str, Any]:
    """Runtime load only — config remembers engine on, not which checkpoint is in VRAM."""
    return update_server_runtime(server_id, engine_on=True, loaded_by=loaded_by)


def release_gpu_checkpoints(server: dict[str, Any]) -> dict[str, Any]:
    """Unload any checkpoints from GPU on a running engine."""
    entry = normalize_server(server)
    host = str(entry.get('host') or '127.0.0.1')
    port = int(entry.get('port') or 0)
    api_url = str(entry.get('api_url') or '')
    if port <= 0 or not tcp_port_open(host, port) or not api_url:
        return {'success': True, 'unloaded': False}
    if is_embedding_server(entry):
        return {
            'success': True,
            'unloaded': False,
            'requires_stop': True,
            'reason': 'embedding engines cannot eject their active model while listening',
        }

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


def release_and_stop_all_managed_engines(*, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Unload checkpoints and stop llama-server for every configured DFlash engine."""
    from core.runtime import stop_server

    config = cfg or load_config()
    results: list[dict[str, Any]] = []
    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        server_id = str(server.get('id') or '')
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        api_url = str(server.get('api_url') or '')
        if port <= 0:
            continue
        release_row = release_gpu_checkpoints(server) if tcp_port_open(host, port) else {'success': True, 'unloaded': False}
        stop_row = stop_server(port=port, host=host, api_url=api_url or None) if tcp_port_open(host, port) else {'success': True, 'stopped': False}
        results.append({
            'server_id': server_id,
            'released': release_row,
            'stopped': stop_row,
        })
    return results


def restore_engines(*, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """On Console boot: restore saved engines and their configured checkpoints."""
    from core.server_boot import adopt_running_engine, start_router_listener
    from core.embedding_server import probe_embedding_models, start_embedding_server

    config = cfg or load_config()
    results: list[dict[str, Any]] = []

    for server in list_servers(config):
        if not server.get('enabled', True):
            continue
        server_id = str(server['id'])
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        api_url = str(server.get('api_url') or '')
        if port <= 0:
            continue

        runtime = get_engine_state(server_id, cfg=config)
        port_open = tcp_port_open(host, port)

        if not runtime.get('engine_on'):
            if port_open:
                from core.runtime import stop_server

                stop_server(port=port, host=host, api_url=api_url or None)
                results.append({'server_id': server_id, 'action': 'stopped_orphan'})
            else:
                results.append({'server_id': server_id, 'action': 'skipped_engine_off'})
            continue

        if port_open:
            adopt = adopt_running_engine(server, cfg=config)
            # Startup must leave GPU memory clear. The user can load a model
            # explicitly from the Models tab after the listener is ready.
            loaded = (
                probe_embedding_models(api_url)
                if is_embedding_server(server) and api_url
                else probe_models(api_url) if api_url else []
            )
            if loaded:
                release = release_gpu_checkpoints(server)
            else:
                release = {'success': True, 'unloaded': False, 'models': []}
            note_engine_idle(server_id)
            action = 'adopted_idle'
            results.append({
                'server_id': server_id,
                'action': action,
                'models': loaded or None,
                'release': release,
                **adopt,
            })
            continue

        started = (
            start_embedding_server(server, cfg=config)
            if is_embedding_server(server)
            else start_router_listener(server, cfg=config)
        )
        if started.get('success'):
            # Start the router only; never repopulate GPU memory during a
            # Console restart.
            note_engine_idle(server_id)
            results.append({'server_id': server_id, 'action': 'restarted_listener', **started})
        else:
            results.append({'server_id': server_id, 'action': 'restart_failed', **started})

    return results
