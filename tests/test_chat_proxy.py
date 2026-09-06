from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from core.chat_proxy import apply_reasoning_policy, aggregate_sse_to_completion, extract_stream_completion_stats, open_upstream_chat_stream, parse_chat_body, prepare_upstream_stream_body, upstream_chat_completion, wants_stream


def test_aggregate_sse_to_completion():
    raw = (
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" world"}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
        b'data: [DONE]\n\n'
    )
    payload = aggregate_sse_to_completion(raw)
    assert payload['choices'][0]['message']['content'] == 'Hello world'
    assert payload['usage']['completion_tokens'] == 2


def test_upstream_chat_completion_forces_stream(monkeypatch):
    captured: dict[str, bytes] = {}

    class FakeResp:
        def __init__(self):
            self.status = 200
            self._lines = [
                b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
                b'data: [DONE]\n\n',
                b'',
            ]
            self._idx = 0
            self.closed = False

        def readline(self):
            if self._idx < len(self._lines):
                line = self._lines[self._idx]
                self._idx += 1
                return line
            return b''

        def close(self):
            self.closed = True

    def fake_urlopen(req, timeout=600):
        captured['body'] = req.data
        return FakeResp()

    monkeypatch.setattr('core.chat_proxy.urllib.request.urlopen', fake_urlopen)
    status, payload = upstream_chat_completion(
        'http://127.0.0.1:8092/v1/chat/completions',
        json.dumps({'messages': [], 'stream': False}).encode(),
        server_id='gemma-12b-ar',
    )
    body = json.loads(captured['body'].decode())
    assert body['stream'] is True
    assert status == 200
    assert payload['choices'][0]['message']['content'] == 'Hi'


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


def test_apply_reasoning_policy_strips_for_non_reasoning_model():
    raw = json.dumps({
        'model': 'nomic-embed',
        'messages': [{'role': 'user', 'content': 'hi'}],
        'reasoning_effort': 'high',
        'thinking': True,
    }).encode()
    out = apply_reasoning_policy(raw, reasoning=False)
    body = json.loads(out)
    assert 'reasoning_effort' not in body
    assert 'thinking' not in body
    assert body['model'] == 'nomic-embed'
    assert body['messages'] == [{'role': 'user', 'content': 'hi'}]


def test_apply_reasoning_policy_keeps_for_reasoning_model():
    raw = json.dumps({
        'model': 'gemma-4-12b-it-qat',
        'messages': [{'role': 'user', 'content': 'hi'}],
        'reasoning_effort': 'high',
    }).encode()
    out = apply_reasoning_policy(raw, reasoning=True)
    body = json.loads(out)
    assert body['reasoning_effort'] == 'high'


def test_apply_reasoning_policy_forces_none_when_disabled():
    raw = json.dumps({
        'model': 'gemma-4-12b-it-qat',
        'messages': [{'role': 'user', 'content': 'hi'}],
        'reasoning_effort': 'high',
        'max_tokens': 20,
    }).encode()
    out = apply_reasoning_policy(raw, reasoning=True, disable_reasoning=True)
    body = json.loads(out)
    assert body['reasoning_effort'] == 'none'
    assert body['max_tokens'] == 64


def test_apply_reasoning_policy_honors_reasoning_effort_none():
    raw = json.dumps({
        'model': 'gemma-4-12b-it-qat',
        'messages': [{'role': 'user', 'content': 'hi'}],
        'reasoning_effort': 'none',
    }).encode()
    out = apply_reasoning_policy(raw, reasoning=True)
    body = json.loads(out)
    assert body['reasoning_effort'] == 'none'


def test_validate_reasoning_chat_request_allows_disabled_low_max_tokens():
    from core.chat_proxy import validate_reasoning_chat_request

    raw = json.dumps({
        'messages': [{'role': 'user', 'content': 'hi'}],
        'max_tokens': 32,
        'reasoning_effort': 'none',
    }).encode()
    assert validate_reasoning_chat_request(raw, reasoning=True, disable_reasoning=False) is None


def test_validate_reasoning_chat_request_blocks_low_max_tokens_without_disable():
    from core.chat_proxy import validate_reasoning_chat_request

    raw = json.dumps({
        'messages': [{'role': 'user', 'content': 'hi'}],
        'max_tokens': 32,
        'reasoning_effort': 'high',
    }).encode()
    assert validate_reasoning_chat_request(raw, reasoning=True, disable_reasoning=False)


def test_apply_reasoning_policy_passthrough_when_no_reasoning_keys():
    raw = json.dumps({'model': 'x', 'messages': []}).encode()
    assert apply_reasoning_policy(raw, reasoning=False) == raw


def test_upstream_chat_completion_success():
    class FakeResp:
        status = 200

        def __init__(self):
            self.closed = False
            self._lines = [
                b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
                b'data: [DONE]\n\n',
                b'',
            ]
            self._idx = 0

        def readline(self) -> bytes:
            if self._idx < len(self._lines):
                line = self._lines[self._idx]
                self._idx += 1
                return line
            return b''

        def close(self) -> None:
            self.closed = True

    fake = FakeResp()
    with patch('core.chat_proxy.urllib.request.urlopen', return_value=fake):
        status, body = upstream_chat_completion(
            'http://127.0.0.1:8092/v1/chat/completions',
            b'{}',
            server_id='gemma-12b-ar',
        )
    assert status == 200
    assert body['choices'][0]['message']['content'] == 'hi'
    assert fake.closed is True


def test_open_upstream_chat_stream_forces_upstream_stream_body():
    import asyncio

    captured: dict[str, bytes] = {}

    class FakeResp:
        headers = {'Content-Type': 'text/event-stream; charset=utf-8'}

        def __init__(self):
            self.closed = False
            self.raw = MagicMock()
            self.raw._fp = MagicMock()

        def readline(self) -> bytes:
            if not hasattr(self, '_lines'):
                self._lines = [
                    b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
                    b'data: [DONE]\n\n',
                    b'',
                ]
                self._idx = 0
            if self._idx < len(self._lines):
                line = self._lines[self._idx]
                self._idx += 1
                return line
            return b''

        def close(self) -> None:
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=600):
        captured['body'] = req.data
        return FakeResp()

    async def run():
        with patch('core.chat_proxy.urllib.request.urlopen', side_effect=fake_urlopen):
            media_type, chunks, close_fn = await open_upstream_chat_stream(
                'http://127.0.0.1:8092/v1/chat/completions',
                json.dumps({'messages': [], 'stream': False}).encode(),
                server_id='stream-body-test',
            )
        assert 'text/event-stream' in media_type
        await asyncio.sleep(0.05)
        out = b''.join([chunk async for chunk in chunks])
        close_fn()
        body = json.loads(captured['body'].decode())
        assert body['stream'] is True
        assert body['stream_options']['include_usage'] is True
        assert b'Hi' in out

    asyncio.run(run())


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
    monkeypatch.setattr('core.runtime.probe_runtime_state', lambda url: (['gemma-4-12b-it-qat'], [], True, None))
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
    monkeypatch.setattr('core.runtime.probe_runtime_state', lambda url: ([], [], True, None))
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
