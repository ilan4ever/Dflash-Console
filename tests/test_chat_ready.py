from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import app


@pytest.fixture
def client():
    return TestClient(app)


def _config_state(server: dict) -> dict:
    return {'servers': [server], 'context_auto_grow': False}


def test_chat_ready_engine_off_is_immediate(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191/v1',
        'enabled': True,
        'engine_on': False,
    }
    state = _config_state(server)
    monkeypatch.setattr('core.config.load_config', lambda: state)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: False)

    resp = client.get('/api/servers/gemma-12b-ar/chat-ready')
    assert resp.status_code == 200
    payload = resp.json()
    assert payload['ready'] is False
    assert payload['reason'] == 'engine_off'


def test_chat_auto_starts_listener_when_engine_off(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191/v1',
        'enabled': True,
        'engine_on': False,
        'target_path': 'C:/models/test.gguf',
        'load_settings': {'gpu_layers': 99},
        'context_size': 8192,
    }
    load_calls: list[str] = []
    port_open = {'value': False}

    def fake_port_open(host, port):
        return port_open['value']

    def fake_start_listener(entry, cfg=None):
        port_open['value'] = True
        return {'success': True}

    def fake_build_status(entry, cfg=None):
        if load_calls:
            return {
                'status': 'loaded',
                'loaded_models': [entry.get('model_id') or 'gemma-4-12b-it-qat'],
                'model_id': entry.get('model_id'),
            }
        if port_open['value']:
            return {'status': 'running', 'loaded_models': [], 'model_id': entry.get('model_id')}
        return {'status': 'stopped', 'loaded_models': []}

    state = _config_state(server)
    monkeypatch.setattr('core.config.load_config', lambda: state)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', fake_port_open)
    monkeypatch.setattr('core.runtime.tcp_port_open', fake_port_open)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr(
        'core.chat_ready.ensure_managed_listen_port',
        lambda server, cfg=None: {'success': True, 'port': int(server.get('port') or 0), 'reason': 'free'},
    )
    monkeypatch.setattr('core.server_boot.start_router_listener', fake_start_listener)
    monkeypatch.setattr('api.app.load_server_checkpoint', lambda *args, **kwargs: load_calls.append('load') or {'success': True})
    monkeypatch.setattr('core.runtime.build_server_status', fake_build_status)
    monkeypatch.setattr('core.memory_guardrails.assess_load', lambda srv, cfg=None: {'level': 'ok'})
    monkeypatch.setattr('core.server_boot.find_target_loaded_elsewhere', lambda *args, **kwargs: None)

    from api.app import _ensure_server_ready_for_chat

    _ensure_server_ready_for_chat('gemma-12b-ar', server, state, client_label='test')

    assert load_calls == ['load']
    assert server['engine_on'] is True


def test_chat_rejects_when_listener_start_fails(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191/v1',
        'enabled': True,
        'engine_on': False,
    }
    load_calls: list[str] = []

    state = _config_state(server)
    monkeypatch.setattr('core.config.load_config', lambda: state)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: False)
    monkeypatch.setattr(
        'core.chat_ready.ensure_managed_listen_port',
        lambda server, cfg=None: {'success': True, 'port': int(server.get('port') or 0), 'reason': 'free'},
    )
    monkeypatch.setattr(
        'core.server_boot.start_router_listener',
        lambda entry, cfg=None: {'success': False, 'error': 'bind failed'},
    )
    monkeypatch.setattr(
        'api.app.load_server_checkpoint',
        lambda *args, **kwargs: load_calls.append('load') or {'success': True},
    )

    resp = client.post(
        '/api/servers/gemma-12b-ar/v1/chat/completions',
        json={'messages': [{'role': 'user', 'content': 'hi'}], 'stream': False},
    )

    assert resp.status_code == 503
    assert load_calls == []
    body = resp.json()
    assert body['detail']['error']['reason'] == 'engine_start_failed'


def test_chat_rejects_images_when_no_mmproj(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191/v1',
        'enabled': True,
        'engine_on': True,
        'target_path': 'C:/models/test.gguf',
    }
    tiny_png = (
        'data:image/png;base64,'
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    )
    state = {'servers': [server], 'context_auto_grow': False}
    monkeypatch.setattr('core.config.load_config', lambda: state)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr(
        'core.chat_vision.ensure_vision_ready_for_chat',
        lambda entry, cfg=None: {
            'success': False,
            'reason': 'no_mmproj',
            'error': 'no projector',
            'supports_vision': False,
            'imageInput': False,
        },
    )

    resp = client.post(
        '/api/servers/gemma-12b-ar/v1/chat/completions',
        json={
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'what is this?'},
                    {'type': 'image_url', 'image_url': {'url': tiny_png}},
                ],
            }],
            'stream': False,
        },
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body['detail']['error']['reason'] == 'no_mmproj'
    assert body['detail']['error']['supports_vision'] is False


def test_chat_ready_reconciles_live_port_when_engine_off_flag_stale(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191/v1',
        'enabled': True,
        'engine_on': False,
    }
    reconcile_calls: list[str] = []

    state = _config_state(server)
    monkeypatch.setattr('core.config.load_config', lambda: state)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr(
        'core.chat_ready.get_engine_state',
        lambda sid, cfg=None: {'engine_on': False},
    )
    monkeypatch.setattr(
        'core.chat_ready._sync_engine_on',
        lambda server, cfg, server_id: reconcile_calls.append(server_id),
    )
    monkeypatch.setattr(
        'core.runtime.build_server_status',
        lambda entry, cfg=None: {
            'status': 'loaded',
            'loaded_models': ['gemma-4-12b-it-qat'],
            'active_model_id': 'gemma-4-12b-it-qat',
        },
    )

    resp = client.get('/api/servers/gemma-12b-ar/chat-ready')
    assert resp.status_code == 200
    payload = resp.json()
    assert payload['ready'] is True
    assert payload['reason'] == 'ready'
    assert reconcile_calls == ['gemma-12b-ar']


def test_chat_ready_reports_model_not_loaded_after_unload(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191/v1',
        'enabled': True,
        'engine_on': True,
    }
    state = _config_state(server)
    monkeypatch.setattr('core.config.load_config', lambda: state)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr(
        'core.runtime.build_server_status',
        lambda entry, cfg=None: {
            'status': 'running',
            'loaded_models': [],
            'active_model_id': '',
        },
    )

    resp = client.get('/api/servers/gemma-12b-ar/chat-ready')
    assert resp.status_code == 200
    payload = resp.json()
    assert payload['ready'] is False
    assert payload['ready_for_chat'] is False
    assert payload['listener_ready'] is True
    assert payload['reason'] == 'model_not_loaded'


def test_chat_ready_reports_ready_when_alias_checkpoint_loaded_elsewhere(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'profile': 'gemma-12-ar',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8191,
        'api_url': 'http://127.0.0.1:8191/v1',
        'enabled': True,
        'engine_on': True,
    }
    other = {
        'id': 'gemma-4-12b-it-q4-k-m-dflash',
        'profile': 'gemma-12-dflash',
        'model_id': 'gemma-4-12b-it-q4-k-m',
        'host': '127.0.0.1',
        'port': 8101,
        'api_url': 'http://127.0.0.1:8101/v1',
        'enabled': True,
        'engine_on': True,
    }
    state = {'servers': [server, other], 'context_auto_grow': False}

    def fake_status(entry, cfg=None, **kwargs):
        if str(entry.get('id') or '') == other['id']:
            return {
                'status': 'loaded',
                'loaded_models': ['gemma-4-12b-it-q4-k-m'],
                'active_model_id': 'gemma-4-12b-it-q4-k-m',
            }
        return {'status': 'running', 'loaded_models': [], 'model_id': server['model_id']}

    monkeypatch.setattr('core.config.load_config', lambda: state)
    monkeypatch.setattr('api.app.load_config', lambda: state)
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server if sid == server['id'] else other)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr('core.runtime.build_server_status', fake_status)
    monkeypatch.setattr(
        'core.server_boot.find_target_loaded_elsewhere',
        lambda srv, cfg=None, exclude_server_id=None: {
            'server_id': other['id'],
            'label': other['id'],
            'port': other['port'],
        },
    )

    resp = client.get('/api/servers/gemma-12b-ar/chat-ready')
    assert resp.status_code == 200
    payload = resp.json()
    assert payload['ready'] is True
    assert payload['ready_for_chat'] is True
    assert payload.get('routed_server_id') == other['id']


def test_ensure_engine_listener_rebinds_foreign_port(monkeypatch):
    server = {
        'id': 'qwen3-8-27b-q6-k-l',
        'label': 'Qwen 27B',
        'host': '127.0.0.1',
        'port': 8090,
        'api_url': 'http://127.0.0.1:8090/v1',
        'enabled': True,
        'engine_on': True,
    }
    started = {'value': False}

    def fake_port_open(host, port):
        if port == 8090:
            return not started['value']
        return started['value']

    def fake_start(entry, cfg=None):
        started['value'] = True
        return {'success': True, 'port': int(entry.get('port') or 0)}

    monkeypatch.setattr('core.chat_ready.tcp_port_open', fake_port_open)
    monkeypatch.setattr(
        'core.chat_ready.ensure_managed_listen_port',
        lambda entry, cfg=None: (
            entry.update({'port': 8097, 'api_url': 'http://127.0.0.1:8097/v1'})
            or {'success': True, 'port': 8097, 'previous_port': 8090, 'reason': 'rebound', 'api_url': 'http://127.0.0.1:8097/v1'}
        ),
    )
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: False)
    monkeypatch.setattr('core.server_boot.start_router_listener', fake_start)
    monkeypatch.setattr('core.chat_ready.note_engine_on', lambda server_id: None)
    monkeypatch.setattr('core.chat_ready.get_server', lambda cfg, sid: server)

    from core.chat_ready import ensure_engine_listener_for_chat

    result = ensure_engine_listener_for_chat(server, cfg={'servers': [server]})
    assert result['success'] is True
    assert result['reason'] == 'started'
    assert server['port'] == 8097
    assert started['value'] is True
