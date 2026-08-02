from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import app


@pytest.fixture
def client():
    return TestClient(app)


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
    monkeypatch.setattr('api.app.load_config', lambda: {'servers': [server]})
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: False)

    resp = client.get('/api/servers/gemma-12b-ar/chat-ready')
    assert resp.status_code == 200
    payload = resp.json()
    assert payload['ready'] is False
    assert payload['reason'] == 'engine_off'


def test_chat_rejects_when_engine_off(client, monkeypatch):
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

    monkeypatch.setattr('api.app.load_config', lambda: {'servers': [server]})
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: False)
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
    assert body['detail']['error']['reason'] == 'engine_off'


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

    monkeypatch.setattr('api.app.load_config', lambda: {'servers': [server]})
    monkeypatch.setattr('api.app._require_server', lambda cfg, sid: server)
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr(
        'core.chat_ready.get_engine_state',
        lambda sid, cfg=None: {'engine_on': False},
    )
    monkeypatch.setattr(
        'core.engine_state.note_engine_on',
        lambda sid: reconcile_calls.append(sid) or {'engine_on': True},
    )

    resp = client.get('/api/servers/gemma-12b-ar/chat-ready')
    assert resp.status_code == 200
    payload = resp.json()
    assert payload['ready'] is True
    assert payload['reason'] == 'ready'
    assert reconcile_calls == ['gemma-12b-ar']
