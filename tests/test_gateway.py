from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx
from fastapi import Request

from api import gateway as gateway_module
from core.chat_proxy import (
    empty_completion_guard,
    prepare_upstream_stream_body,
    sse_had_content_delta,
    sse_stream_complete,
    sse_stream_error_chunk,
    wants_stream,
)


def test_prepare_upstream_stream_body_forces_stream_and_usage():
    raw = json.dumps({'messages': [], 'stream': False}).encode()
    body = json.loads(prepare_upstream_stream_body(raw).decode())
    assert body['stream'] is True
    assert body['stream_options']['include_usage'] is True


def test_sse_stream_complete_detects_done_marker():
    raw = b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n'
    assert sse_stream_complete(raw) is True
    assert sse_stream_complete(b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n') is False


def test_sse_had_content_delta():
    raw = b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n'
    assert sse_had_content_delta(raw) is False
    raw = b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
    assert sse_had_content_delta(raw) is True


def test_empty_completion_guard_reasoning_only():
    payload = {
        'choices': [{
            'message': {'role': 'assistant', 'content': '', 'reasoning_content': 'thoughts'},
            'finish_reason': 'length',
        }],
    }
    message = empty_completion_guard(payload)
    assert message is not None
    assert 'max_tokens' in message


def test_empty_completion_guard_allows_content():
    payload = {
        'choices': [{
            'message': {'role': 'assistant', 'content': 'Hello'},
            'finish_reason': 'stop',
        }],
    }
    assert empty_completion_guard(payload) is None


def test_sse_stream_error_chunk_terminates():
    chunk = sse_stream_error_chunk('boom')
    assert b'"type": "stream_error"' in chunk
    assert chunk.endswith(b'data: [DONE]\n\n')


def test_wants_stream_from_body_bytes():
    assert wants_stream(json.dumps({'stream': True}).encode()) is True
    assert wants_stream(json.dumps({'stream': False}).encode()) is False


def test_forward_chat_uses_body_for_stream_flag():
    body = json.dumps({'model': 'demo', 'messages': [], 'stream': True}).encode()
    request = Request({'type': 'http', 'method': 'POST', 'headers': [], 'path': '/v1/chat/completions'})

    class FakeUpstream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
            yield b'data: [DONE]\n\n'

    class FakeClient:
        def stream(self, method, url, content=None, headers=None):
            assert content == body
            return FakeUpstream()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def run():
        with patch('api.gateway.httpx.AsyncClient', return_value=FakeClient()):
            response = await gateway_module._forward_chat(
                request,
                'http://127.0.0.1:8900/api/servers/demo/v1/chat/completions',
                body,
            )
            assert response.media_type == 'text/event-stream'
            chunks = [part async for part in response.body_iterator]
        joined = b''.join(chunks)
        assert b'OK' in joined

    asyncio.run(run())


def test_forward_chat_stream_drop_emits_terminal_sse():
    body = json.dumps({'model': 'demo', 'messages': [], 'stream': True}).encode()
    request = Request({'type': 'http', 'method': 'POST', 'headers': [], 'path': '/v1/chat/completions'})

    class BrokenUpstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            if False:
                yield b''
            raise httpx.RemoteProtocolError('peer closed connection without sending complete message body')

    class FakeClient:
        def stream(self, method, url, content=None, headers=None):
            return BrokenUpstream()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def run():
        with patch('api.gateway.httpx.AsyncClient', return_value=FakeClient()):
            response = await gateway_module._forward_chat(
                request,
                'http://127.0.0.1:8900/api/servers/demo/v1/chat/completions',
                body,
            )
            chunks = [part async for part in response.body_iterator]
        joined = b''.join(chunks)
        assert b'stream_error' in joined
        assert b'[DONE]' in joined

    asyncio.run(run())
