from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from api.app import _ensure_server_ready_for_chat


def _server() -> dict:
    return {
        'id': 'underdog-saluki-27b-1-0-iq2-mix',
        'model_id': 'underdog-saluki-27b-1.0-iq2-mix',
        'host': '127.0.0.1',
        'port': 8099,
        'api_url': 'http://127.0.0.1:8099/v1',
        'target_path': r'C:\models\underdog.gguf',
        'enabled': True,
        'engine_on': True,
    }


def _patch_idle(monkeypatch: pytest.MonkeyPatch, server: dict, cfg: dict) -> None:
    monkeypatch.setattr('api.app.load_config', lambda: cfg)
    monkeypatch.setattr('core.config.load_config', lambda: cfg)
    monkeypatch.setattr(
        'core.config.get_server',
        lambda config, server_id: server if server_id == server['id'] else None,
    )
    monkeypatch.setattr('core.chat_ready.get_engine_state', lambda sid, cfg=None: {'engine_on': True})
    monkeypatch.setattr('core.chat_ready.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('api.app.tcp_port_open', lambda host, port: True)
    monkeypatch.setattr('core.chat_ready.listener_is_managed_engine', lambda host, port: True)
    monkeypatch.setattr(
        'core.chat_ready.ensure_managed_listen_port',
        lambda entry, cfg=None: {'success': True, 'port': entry['port'], 'reason': 'ours'},
    )
    monkeypatch.setattr('core.memory_guardrails.assess_load', lambda *args, **kwargs: {'level': 'ok'})
    monkeypatch.setattr('api.app._auto_stop_other_servers', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        'core.engine_state.note_engine_active_client',
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr('core.engine_state.note_engine_loaded', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        'core.server_boot.find_target_loaded_elsewhere',
        lambda *args, **kwargs: None,
    )


def test_failed_jit_load_is_retryable_503(monkeypatch: pytest.MonkeyPatch):
    server = _server()
    cfg = {'servers': [server], 'context_auto_grow': False}
    _patch_idle(monkeypatch, server, cfg)
    empty = {'status': 'running', 'loaded_models': [], 'model_id': server['model_id']}
    monkeypatch.setattr('core.runtime.build_server_status', lambda *args, **kwargs: empty)
    monkeypatch.setattr('core.runtime.probe_runtime_state', lambda url: ([], [], True, None, True))
    monkeypatch.setattr(
        'api.app.load_server_checkpoint',
        lambda *args, **kwargs: {'success': False, 'error': 'backend rejected checkpoint'},
    )

    with pytest.raises(HTTPException) as exc:
        _ensure_server_ready_for_chat(server['id'], server, cfg)

    assert exc.value.status_code == 503
    assert exc.value.detail['error'] == 'model_load_failed'
    assert exc.value.detail['reason'] == 'model_load_failed'
    assert exc.value.detail['retryable'] is True


def test_jit_timeout_never_forwards_unloaded_model(monkeypatch: pytest.MonkeyPatch):
    server = _server()
    cfg = {'servers': [server], 'context_auto_grow': False}
    _patch_idle(monkeypatch, server, cfg)
    empty = {'status': 'running', 'loaded_models': [], 'model_id': server['model_id']}
    monkeypatch.setattr('core.runtime.build_server_status', lambda *args, **kwargs: empty)
    monkeypatch.setattr('core.runtime.probe_runtime_state', lambda url: ([], [server['model_id']], True, 0.1, True))
    monkeypatch.setattr('api.app.load_server_checkpoint', lambda *args, **kwargs: {'success': True})
    monkeypatch.setattr(time, 'sleep', lambda *_args, **_kwargs: None)
    clock = {'now': 0.0}

    def advance_time() -> float:
        clock['now'] += 181.0
        return clock['now']

    monkeypatch.setattr(time, 'time', advance_time)

    with pytest.raises(HTTPException) as exc:
        _ensure_server_ready_for_chat(server['id'], server, cfg)

    assert exc.value.status_code == 503
    assert exc.value.detail['error'] == 'model_not_loaded'
    assert exc.value.detail['reason'] == 'model_load_timeout'
    assert exc.value.detail['retryable'] is True


def test_stale_loaded_status_is_verified_against_runtime(monkeypatch: pytest.MonkeyPatch):
    server = _server()
    cfg = {'servers': [server], 'context_auto_grow': False}
    _patch_idle(monkeypatch, server, cfg)
    stale = {
        'status': 'loaded',
        'loaded_models': [server['model_id']],
        'active_model_id': server['model_id'],
        'model_id': server['model_id'],
    }
    monkeypatch.setattr('core.runtime.build_server_status', lambda *args, **kwargs: stale)
    monkeypatch.setattr('core.runtime.probe_runtime_state', lambda url: ([], [], True, None, True))
    monkeypatch.setattr('api.app.load_server_checkpoint', lambda *args, **kwargs: {'success': True})
    monkeypatch.setattr(time, 'sleep', lambda *_args, **_kwargs: None)
    clock = {'now': 0.0}

    def advance_time() -> float:
        clock['now'] += 181.0
        return clock['now']

    monkeypatch.setattr(time, 'time', advance_time)

    with pytest.raises(HTTPException) as exc:
        _ensure_server_ready_for_chat(server['id'], server, cfg)

    assert exc.value.status_code == 503
    assert exc.value.detail['reason'] == 'model_load_timeout'


def test_unloaded_model_loads_when_request_context_exceeds_configured_cap(monkeypatch: pytest.MonkeyPatch):
    server = _server()
    cfg = {'servers': [server], 'context_auto_grow': True}
    _patch_idle(monkeypatch, server, cfg)
    loaded = {'done': False}
    empty = {'status': 'running', 'loaded_models': [], 'model_id': server['model_id']}
    monkeypatch.setattr('core.runtime.build_server_status', lambda *args, **kwargs: empty)
    monkeypatch.setattr(
        'core.runtime.probe_runtime_state',
        lambda url: ([server['model_id']], [], True, None, True)
        if loaded['done'] else ([], [], True, None, True),
    )
    monkeypatch.setattr('api.app._grow_context_for_chat', lambda *args, **kwargs: pytest.fail('must not grow an unloaded model'))

    def load_checkpoint(*args, **kwargs):
        loaded['done'] = True
        return {'success': True}

    monkeypatch.setattr('api.app.load_server_checkpoint', load_checkpoint)

    live = _ensure_server_ready_for_chat(server['id'], server, cfg, required_context=262144)

    assert live['loaded_models'] == [server['model_id']]
