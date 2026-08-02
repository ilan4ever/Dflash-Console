from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from core.chat_proxy import extract_stream_completion_stats, open_upstream_chat_stream, parse_chat_body, upstream_chat_completion, wants_stream


def test_extract_stream_completion_stats():
    raw = (
        b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":17,"completion_tokens":12,"total_tokens":29},'
        b'"timings":{"predicted_per_second":31.2}}\n\n'
        b'data: [DONE]\n\n'
    )
    payload = extract_stream_completion_stats(raw)
    assert payload is not None
    assert payload['usage']['completion_tokens'] == 12


def test_wants_stream_true():
    raw = json.dumps({'messages': [], 'stream': True}).encode()
    assert wants_stream(raw) is True


def test_wants_stream_false():
    raw = json.dumps({'messages': [], 'stream': False}).encode()
    assert wants_stream(raw) is False


def test_upstream_chat_completion_success():
    payload = {'choices': [{'message': {'content': 'hi'}}]}
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)

    with patch('core.chat_proxy.urllib.request.urlopen', return_value=resp):
        status, body = upstream_chat_completion('http://127.0.0.1:8092/v1/chat/completions', b'{}')
    assert status == 200
    assert body == payload


def test_open_upstream_chat_stream_yields_chunks():
    import asyncio

    class FakeResp:
        headers = {'Content-Type': 'text/event-stream; charset=utf-8'}

        def __init__(self):
            self.closed = False
            self.raw = MagicMock()
            self.raw._fp = MagicMock()

        def readline(self) -> bytes:
            if not hasattr(self, '_sent'):
                self._sent = True
                return b'data: {"x":1}\n\n'
            return b''

        def close(self) -> None:
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    async def run():
        fake = FakeResp()
        with patch('core.chat_proxy.urllib.request.urlopen', return_value=fake):
            media_type, chunks, close_fn = await open_upstream_chat_stream(
                'http://127.0.0.1:8092/v1/chat/completions',
                b'{}',
                server_id='gemma-31b-dflash',
            )
        assert 'text/event-stream' in media_type
        # Let the worker thread enqueue the first SSE line before we drain.
        await asyncio.sleep(0.05)
        out = b''.join([chunk async for chunk in chunks])
        assert b'data:' in out
        close_fn()
        assert fake.closed is True

    asyncio.run(run())


def test_cancel_active_upstream_streams_closes_bound_response():
    from core.chat_proxy import UpstreamChatStream, cancel_active_upstream_streams, _register_upstream, _unregister_upstream

    stream = UpstreamChatStream(server_id='cancel-test')
    fake = MagicMock()
    fake.raw = MagicMock()
    fake.raw._fp = MagicMock()
    stream.bind_response(fake)
    _register_upstream(stream)
    closed = cancel_active_upstream_streams('cancel-test')
    assert closed == 1
    fake.close.assert_called()
    _unregister_upstream(stream)


def test_build_server_status_exposes_agent_fields(monkeypatch):
    from core.runtime import build_server_status

    server = {
        'id': 'gemma-12b-ar',
        'label': 'Gemma 12B',
        'model_id': 'gemma-4-12b-it-qat',
        'host': '127.0.0.1',
        'port': 8092,
        'api_url': 'http://127.0.0.1:8092/v1',
        'engine_on': True,
    }

    monkeypatch.setattr('core.runtime.tcp_port_open', lambda h, p: True)
    monkeypatch.setattr('core.runtime.probe_runtime_state', lambda url: (['gemma-4-12b-it-qat'], [], True))
    monkeypatch.setattr('core.runtime.read_log_tail', lambda sid: [])
    monkeypatch.setattr('core.runtime.is_active_boot', lambda lines: False)
    monkeypatch.setattr('core.runtime.get_started_launch', lambda port: None)
    monkeypatch.setattr('core.runtime.adopt_running_engine', lambda s, cfg=None: None)
    monkeypatch.setattr('core.runtime.resolve_model_stack', lambda s, cfg=None: [])
    monkeypatch.setattr('core.runtime.query_gpu_devices', lambda: [])
    monkeypatch.setattr('core.inference_stats.fetch_inference_stats', lambda url, server_id='', model_id='': {})

    live = build_server_status(server, cfg={})
    assert live['model_id'] == 'gemma-4-12b-it-qat'
    assert live['status'] == 'loaded'
    assert live['loaded_models'] == ['gemma-4-12b-it-qat']
    assert live['ready_for_chat'] is True
    assert live['active_model_id'] == 'gemma-4-12b-it-qat'


def test_build_server_status_running_not_ready(monkeypatch):
    from core.runtime import build_server_status

    server = {
        'id': 'gemma-12b-ar',
        # Empty model_id so empty probe is not backfilled to "loaded".
        'model_id': '',
        'host': '127.0.0.1',
        'port': 8092,
        'api_url': 'http://127.0.0.1:8092/v1',
        'engine_on': True,
    }

    monkeypatch.setattr('core.runtime.tcp_port_open', lambda h, p: True)
    monkeypatch.setattr('core.runtime.probe_runtime_state', lambda url: ([], [], True))
    monkeypatch.setattr('core.runtime.read_log_tail', lambda sid: [])
    monkeypatch.setattr('core.runtime.is_active_boot', lambda lines: False)
    monkeypatch.setattr('core.runtime.get_started_launch', lambda port: None)
    monkeypatch.setattr('core.runtime.adopt_running_engine', lambda s, cfg=None: None)
    monkeypatch.setattr('core.runtime.resolve_model_stack', lambda s, cfg=None: [])
    monkeypatch.setattr('core.runtime.query_gpu_devices', lambda: [])

    live = build_server_status(server, cfg={})
    assert live['status'] == 'running'
    assert live['loaded_models'] == []
    assert live['ready_for_chat'] is False
