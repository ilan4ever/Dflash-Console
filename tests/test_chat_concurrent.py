"""Concurrent chat on one loaded engine must not JIT-load into HTTP 409."""

from __future__ import annotations

import asyncio
import json
import time
import pytest
from fastapi import HTTPException

from api.app import _ensure_server_ready_for_chat
from core.chat_queue import chat_server_gate, reset_chat_server_gates_for_tests


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


@pytest.fixture(autouse=True)
def _test_config_only(tmp_path, monkeypatch):
    """Every config read in this file sees only the test engine.

    _ensure_server_ready_for_chat and the modules it calls reload config.json.
    Reading the developer's real file made the result depend on their engines.
    """
    path = tmp_path / 'config.json'
    path.write_text(
        json.dumps({'ui_port': 8900, 'gateway_port': 8001, 'servers': [_idle_server()]}),
        encoding='utf-8',
    )
    monkeypatch.setattr('core.config.CONFIG_PATH', path)


def _patch_listener(monkeypatch):
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
    monkeypatch.setattr('core.engine_state.note_engine_active_client', lambda sid, client_label='', **kwargs: None)
    monkeypatch.setattr('api.app.tcp_port_open', lambda host, port: True)


def test_ensure_ready_does_not_synthesize_readiness_while_generating(monkeypatch):
    server = _idle_server()
    empty = {
        'status': 'running',
        'loaded_models': [],
        'model_id': 'gemma-4-12b-it-qat',
        'ready_for_chat': False,
    }
    load_calls: list[str] = []

    monkeypatch.setattr('core.runtime.build_server_status', lambda srv, cfg=None, **kwargs: empty)
    monkeypatch.setattr('core.inference_stats.is_proxy_generating', lambda sid: True)
    monkeypatch.setattr(
        'api.app.load_server_checkpoint',
        lambda srv, cfg=None, **kwargs: load_calls.append(str(srv.get('id') or '')) or {'success': True},
    )
    monkeypatch.setattr(
        'core.runtime.probe_runtime_state',
        lambda api_url: ([], [], True, None, True),
    )
    _patch_listener(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        _ensure_server_ready_for_chat('gemma-12b-ar', server, {'servers': [server]})
    assert load_calls == []
    assert exc.value.status_code == 503
    assert exc.value.detail['reason'] == 'model_not_loaded'


@pytest.mark.xfail(
    strict=True,
    reason=(
        'BUG: after the status reports the model loaded, _ensure_server_ready_for_chat probes '
        'once more and JIT-loads when that probe is empty (engine busy). It retries only the '
        'status, not the probe. This test used to pass only on a developer config that had '
        "another engine with the same model (the 'loaded elsewhere' branch)."
    ),
)
def test_ensure_ready_retries_busy_probe_before_jit(monkeypatch):
    server = _idle_server()
    empty = {
        'status': 'running',
        'loaded_models': [],
        'model_id': 'gemma-4-12b-it-qat',
    }
    loaded = {
        'status': 'loaded',
        'loaded_models': ['gemma-4-12b-it-qat'],
        'active_model_id': 'gemma-4-12b-it-qat',
    }
    calls = {'n': 0}
    load_calls: list[str] = []

    def _status(srv, cfg=None, **kwargs):
        calls['n'] += 1
        return empty if calls['n'] < 3 else loaded

    probes = {'n': 0}

    def _probe(api_url):
        probes['n'] += 1
        if probes['n'] < 3:
            return [], [], True, None, True
        return ['gemma-4-12b-it-qat'], [], True, None, True

    monkeypatch.setattr('core.runtime.build_server_status', _status)
    monkeypatch.setattr('core.runtime.probe_runtime_state', _probe)
    monkeypatch.setattr('core.inference_stats.is_proxy_generating', lambda sid: False)
    monkeypatch.setattr(
        'api.app.load_server_checkpoint',
        lambda srv, cfg=None, **kwargs: load_calls.append('x') or {'success': True},
    )
    monkeypatch.setattr('time.sleep', lambda *_a, **_k: None)
    _patch_listener(monkeypatch)

    live = _ensure_server_ready_for_chat('gemma-12b-ar', server, {'servers': [server]})
    assert load_calls == []
    assert live['loaded_models'] == ['gemma-4-12b-it-qat']
    assert calls['n'] >= 3


def test_chat_server_gate_serializes_waiters():
    reset_chat_server_gates_for_tests()
    order: list[str] = []
    gate = chat_server_gate('gemma-12b-ar')

    async def _run():
        async def _holder(name: str, hold: float):
            await gate.acquire()
            order.append(f'{name}-in')
            await asyncio.sleep(hold)
            order.append(f'{name}-out')
            gate.release()

        await asyncio.gather(_holder('a', 0.05), _holder('b', 0.01))

    asyncio.run(_run())
    assert order in (
        ['a-in', 'a-out', 'b-in', 'b-out'],
        ['b-in', 'b-out', 'a-in', 'a-out'],
    )

