from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_chat_auto_loads_idle_engine(client, monkeypatch):
    server = {
        'id': 'gemma-12b-ar',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8092,
        'api_url': 'http://127.0.0.1:8092/v1',
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
        'loaded_models': ['gemma-4-12b-it-qat'],
        'model_id': 'gemma-4-12b-it-qat',
        'ready_for_chat': True,
    }
    statuses = [idle, loaded]
    load_calls: list[str] = []

    monkeypatch.setattr('api.app.load_config', lambda: {'servers': [server]})
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr(
        'core.runtime.build_server_status',
        lambda srv, cfg=None, **kwargs: statuses.pop(0) if statuses else loaded,
    )
    monkeypatch.setattr(
        'core.memory_guardrails.assess_load',
        lambda srv, cfg=None: {'level': 'ok'},
    )

    def _fake_load(srv, cfg=None, **kwargs):
        load_calls.append(str(srv.get('id') or ''))
        return {'success': True, 'loaded': True, 'model': 'gemma-4-12b-it-qat'}

    monkeypatch.setattr('api.app.load_server_checkpoint', _fake_load)
    monkeypatch.setattr('core.engine_state.note_engine_loaded', lambda sid, **kwargs: None)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(
        'core.engine_state.get_engine_state',
        lambda sid, cfg=None: {'engine_on': True},
    )

    with patch('core.chat_proxy.upstream_chat_completion', return_value=(200, {'choices': [{'message': {'content': 'ok'}}]})):
        resp = client.post(
            '/api/servers/gemma-12b-ar/v1/chat/completions',
            json={'messages': [{'role': 'user', 'content': 'hi'}], 'stream': False},
        )

    assert resp.status_code == 200
    assert resp.json()['choices'][0]['message']['content'] == 'ok'
    assert load_calls == ['gemma-12b-ar']
