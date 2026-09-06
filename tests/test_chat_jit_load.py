from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import _ensure_server_ready_for_chat, app


@pytest.fixture
def client():
    return TestClient(app)


def _idle_server():
    return {
        'id': 'gemma-12b-ar',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8092,
        'api_url': 'http://127.0.0.1:8092/v1',
        'enabled': True,
        'engine_on': True,
    }


def test_ensure_ready_loads_idle_engine(monkeypatch):
    server = _idle_server()
    idle = {
        'status': 'offline',
        'loaded_models': [],
        'model_id': 'gemma-4-12b-it-qat',
        'ready_for_chat': False,
    }
    loaded = {
        'status': 'loaded',
        'loaded_models': ['gemma-4-12b-it-qat'],
        'model_id': 'gemma-4-12b-it-qat',
        'ready_for_chat': True,
    }
    loaded_flag = {'done': False}
    load_calls: list[str] = []

    monkeypatch.setattr(
        'core.runtime.build_server_status',
        lambda srv, cfg=None, **kwargs: loaded if loaded_flag['done'] else idle,
    )
    monkeypatch.setattr('core.chat_ready.get_engine_state', lambda sid, cfg=None: {'engine_on': True})
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr(
        'core.chat_ready.ensure_managed_listen_port',
        lambda server, cfg=None: {
            'success': True,
            'port': int(server.get('port') or 0),
            'reason': 'ours',
        },
    )
    monkeypatch.setattr('core.memory_guardrails.assess_load', lambda srv, cfg=None: {'level': 'ok'})
    monkeypatch.setattr('core.engine_state.note_engine_loaded', lambda sid, **kwargs: None)

    def _fake_load(srv, cfg=None, **kwargs):
        load_calls.append(str(srv.get('id') or ''))
        loaded_flag['done'] = True
        return {'success': True, 'loaded': True, 'model': 'gemma-4-12b-it-qat'}

    monkeypatch.setattr('api.app.load_server_checkpoint', _fake_load)

    live = _ensure_server_ready_for_chat('gemma-12b-ar', server, {'servers': [server]})
    assert load_calls == ['gemma-12b-ar']
    assert live['status'] == 'loaded'


def test_ensure_ready_notes_active_client_when_already_loaded(monkeypatch):
    server = _idle_server()
    loaded = {
        'status': 'loaded',
        'loaded_models': ['gemma-4-12b-it-qat'],
        'model_id': 'gemma-4-12b-it-qat',
        'ready_for_chat': True,
    }
    active_calls: list[tuple[str, str]] = []

    monkeypatch.setattr('core.runtime.build_server_status', lambda srv, cfg=None, **kwargs: loaded)
    monkeypatch.setattr('core.chat_ready.get_engine_state', lambda sid, cfg=None: {'engine_on': True})
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr(
        'core.chat_ready.ensure_managed_listen_port',
        lambda server, cfg=None: {
            'success': True,
            'port': int(server.get('port') or 0),
            'reason': 'ours',
        },
    )
    monkeypatch.setattr(
        'core.engine_state.note_engine_active_client',
        lambda sid, client_label='': active_calls.append((sid, client_label)) or {'loaded_by_changed': True},
    )

    live = _ensure_server_ready_for_chat(
        'gemma-12b-ar',
        server,
        {'servers': [server]},
        client_label='OneVoice',
    )
    assert live['status'] == 'loaded'
    assert active_calls == [('gemma-12b-ar', 'OneVoice')]


def test_chat_auto_loads_idle_engine(client, monkeypatch):
    server = _idle_server()
    loaded = {
        'status': 'loaded',
        'loaded_models': ['gemma-4-12b-it-qat'],
        'model_id': 'gemma-4-12b-it-qat',
        'active_model_id': 'gemma-4-12b-it-qat',
        'ready_for_chat': True,
    }
    load_calls: list[str] = []

    def _ready(*_args, **_kwargs):
        load_calls.append(server['id'])
        return loaded

    monkeypatch.setattr('api.app.load_config', lambda: {'servers': [server]})
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('api.app._ensure_server_ready_for_chat', _ready)

    with patch('core.chat_proxy.upstream_chat_completion', return_value=(200, {'choices': [{'message': {'content': 'ok'}}]})):
        resp = client.post(
            '/api/servers/gemma-12b-ar/v1/chat/completions',
            json={'messages': [{'role': 'user', 'content': 'hi'}], 'stream': False},
        )

    assert resp.status_code == 200
    assert resp.json()['choices'][0]['message']['content'] == 'ok'
    assert load_calls == ['gemma-12b-ar']


def test_ensure_ready_reuses_checkpoint_loaded_on_other_engine(monkeypatch):
    server = _idle_server()
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
    idle = {
        'status': 'running',
        'loaded_models': [],
        'model_id': 'gemma-4-12b-it-qat',
        'ready_for_chat': False,
    }
    loaded = {
        'status': 'loaded',
        'loaded_models': ['gemma-4-12b-it-q4-k-m'],
        'model_id': 'gemma-4-12b-it-q4-k-m',
        'ready_for_chat': True,
    }
    cfg = {'servers': [server, other], 'context_auto_grow': False}
    active_calls: list[tuple[str, str]] = []

    monkeypatch.setattr('core.config.load_config', lambda: cfg)
    monkeypatch.setattr('api.app.load_config', lambda: cfg)
    monkeypatch.setattr(
        'core.runtime.build_server_status',
        lambda srv, cfg=None, **kwargs: loaded if str(srv.get('id') or '') == other['id'] else idle,
    )
    monkeypatch.setattr('core.chat_ready.get_engine_state', lambda sid, cfg=None: {'engine_on': True})
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr(
        'core.chat_ready.ensure_managed_listen_port',
        lambda server, cfg=None: {
            'success': True,
            'port': int(server.get('port') or 0),
            'reason': 'ours',
        },
    )
    monkeypatch.setattr(
        'core.config.get_server',
        lambda config, sid: other if sid == other['id'] else server,
    )
    monkeypatch.setattr(
        'core.server_boot.find_target_loaded_elsewhere',
        lambda srv, cfg=None, exclude_server_id=None: {
            'server_id': other['id'],
            'label': other['id'],
            'port': other['port'],
        },
    )
    monkeypatch.setattr(
        'core.engine_state.note_engine_active_client',
        lambda sid, client_label='': active_calls.append((sid, client_label)) or {'loaded_by_changed': True},
    )
    monkeypatch.setattr('api.app.load_server_checkpoint', lambda *args, **kwargs: {'success': False})

    live = _ensure_server_ready_for_chat('gemma-12b-ar', server, cfg, client_label='OneVoice')
    assert live['status'] == 'loaded'
    assert server['api_url'] == other['api_url']
    assert active_calls == [(other['id'], 'OneVoice')]
