from __future__ import annotations

from fastapi import HTTPException

import api.app as app


def _cfg(profile: str = 'gemma-chat') -> dict:
    return {
        'ui_port': 8900,
        'servers': [{
            'id': 'engine-a',
            'label': 'Engine A',
            'profile': profile,
            'port': 8090,
            'host': '127.0.0.1',
            'api_url': 'http://127.0.0.1:8090/v1',
            'model_id': 'demo',
            'engine_on': True,
        }],
    }


def test_router_unload_keeps_listener_ready(monkeypatch):
    monkeypatch.setattr(app, 'load_config', lambda: _cfg())
    monkeypatch.setattr(app, 'tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(app, 'unload_model', lambda **kwargs: {'success': True, 'unloaded': True})
    monkeypatch.setattr('core.runtime.probe_models', lambda api_url: ['active-model'])
    monkeypatch.setattr('core.load_progress.append_log', lambda *args, **kwargs: None)
    monkeypatch.setattr('core.engine_state.note_engine_idle', lambda *args, **kwargs: None)

    result = app.server_unload('engine-a')

    assert result['success'] is True
    assert result['engine_stopped'] is False
    assert result['listener_ready'] is True


def test_legacy_unload_transitions_to_idle_router(monkeypatch):
    monkeypatch.setattr(app, 'load_config', lambda: _cfg())
    monkeypatch.setattr(app, 'tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(app, 'unload_model', lambda **kwargs: {
        'success': False,
        'http_status': 404,
        'error': 'not found',
    })
    monkeypatch.setattr('core.runtime.probe_models', lambda api_url: ['active-model'])
    monkeypatch.setattr('core.server_boot.eject_to_router_idle', lambda *args, **kwargs: {'success': True})
    monkeypatch.setattr('core.load_progress.append_log', lambda *args, **kwargs: None)
    monkeypatch.setattr('core.engine_state.note_engine_idle', lambda *args, **kwargs: None)

    result = app.server_unload('engine-a')

    assert result['success'] is True
    assert result['engine_stopped'] is False
    assert result['listener_ready'] is True


def test_embedding_unload_explains_required_stop(monkeypatch):
    monkeypatch.setattr(app, 'load_config', lambda: _cfg('nomic-embed'))
    monkeypatch.setattr(app, 'tcp_port_open', lambda host, port: True)

    try:
        app.server_unload('engine-a')
    except HTTPException as exc:
        assert exc.status_code == 409
        assert 'Stop' in str(exc.detail)
    else:
        raise AssertionError('embedding unload should require an explicit stop')
