"""Proxy chat/completions to llama-server (JSON and SSE streaming)."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from typing import Any, Callable


_ACTIVE_UPSTREAM_LOCK = threading.Lock()
_ACTIVE_UPSTREAM_BY_SERVER: dict[str, set['UpstreamChatStream']] = {}


def parse_chat_body(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode('utf-8', errors='replace') or '{}')
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def wants_stream(raw: bytes) -> bool:
    return parse_chat_body(raw).get('stream') is True


def apply_reasoning_policy(raw: bytes, *, reasoning: bool) -> bytes:
    """Rewrite a chat request body's reasoning controls for the target model.

    Non-reasoning models must behave like a plain chat model: drop
    ``reasoning_effort`` (and any thinking toggles) so the API never asks for
    reasoning and the engine keeps its regular output. Reasoning models keep
    the client's ``reasoning_effort`` untouched so external applications can
    steer it (the running engine's ``--reasoning``/``--reasoning-budget`` flags
    from the per-model runtime setting control the actual thinking budget).
    """
    if not raw or reasoning:
        return raw
    try:
        body = json.loads(raw.decode('utf-8', errors='replace'))
    except json.JSONDecodeError:
        return raw
    if not isinstance(body, dict):
        return raw
    changed = False
    for key in ('reasoning_effort', 'thinking', 'enable_thinking'):
        if key in body:
            body.pop(key, None)
            changed = True
    if not changed:
        return raw
    try:
        return json.dumps(body).encode('utf-8')
    except (TypeError, ValueError):
        return raw


def extract_stream_completion_stats(raw: bytes) -> dict[str, Any] | None:
    """Return the last OpenAI-style payload from an SSE stream that includes usage."""
    if not raw:
        return None
    last: dict[str, Any] | None = None
    for line in raw.decode('utf-8', errors='replace').splitlines():
        if not line.startswith('data:'):
            continue
        payload = line[5:].strip()
        if not payload or payload == '[DONE]':
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get('usage'), dict):
            last = parsed
    return last


def upstream_chat_completion(url: str, raw: bytes, *, content_type: str = 'application/json') -> tuple[int, dict[str, Any]]:
    upstream = urllib.request.Request(url, data=raw, method='POST', headers={'Content-Type': content_type})
    try:
        with urllib.request.urlopen(upstream, timeout=600) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
            return resp.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        try:
            body = json.loads(detail)
        except json.JSONDecodeError:
            body = {'error': detail or str(exc)}
        return exc.code, body if isinstance(body, dict) else {'error': str(body)}


class UpstreamChatStream:
    """Cancellable upstream SSE reader. Closing this aborts llama-server generation."""

    def __init__(self, server_id: str = '') -> None:
        self.server_id = str(server_id or '').strip()
        self.cancel = threading.Event()
        self._resp = None
        self._resp_lock = threading.Lock()
        self._closed = False

    def bind_response(self, resp: Any) -> None:
        with self._resp_lock:
            self._resp = resp
            if self.cancel.is_set() or self._closed:
                self._force_close_resp(resp)

    def close(self) -> None:
        self.cancel.set()
        with self._resp_lock:
            self._closed = True
            resp = self._resp
            self._resp = None
        if resp is not None:
            self._force_close_resp(resp)

    @staticmethod
    def _force_close_resp(resp: Any) -> None:
        try:
            resp.close()
        except Exception:
            pass
        try:
            resp.raw.close()
        except Exception:
            pass
        try:
            resp.raw._fp.close()
        except Exception:
            pass


def _register_upstream(stream: UpstreamChatStream) -> None:
    sid = stream.server_id
    if not sid:
        return
    with _ACTIVE_UPSTREAM_LOCK:
        bucket = _ACTIVE_UPSTREAM_BY_SERVER.get(sid)
        if bucket is None:
            bucket = set()
            _ACTIVE_UPSTREAM_BY_SERVER[sid] = bucket
        bucket.add(stream)


def _unregister_upstream(stream: UpstreamChatStream) -> None:
    sid = stream.server_id
    if not sid:
        return
    with _ACTIVE_UPSTREAM_LOCK:
        bucket = _ACTIVE_UPSTREAM_BY_SERVER.get(sid)
        if not bucket:
            return
        bucket.discard(stream)
        if not bucket:
            _ACTIVE_UPSTREAM_BY_SERVER.pop(sid, None)


def cancel_active_upstream_streams(server_id: str) -> int:
    """Force-close all active Console→llama streams for one engine."""
    sid = str(server_id or '').strip()
    if not sid:
        return 0
    with _ACTIVE_UPSTREAM_LOCK:
        streams = list(_ACTIVE_UPSTREAM_BY_SERVER.get(sid) or ())
    for stream in streams:
        stream.close()
    return len(streams)


def _stream_worker(
    url: str,
    raw: bytes,
    content_type: str,
    loop: asyncio.AbstractEventLoop,
    out_q: asyncio.Queue,
    stream: UpstreamChatStream,
) -> None:
    def _put(item: tuple[str, Any]) -> None:
        if stream.cancel.is_set():
            return
        try:
            loop.call_soon_threadsafe(out_q.put_nowait, item)
        except RuntimeError:
            # Event loop already closed after client disconnect.
            pass

    try:
        upstream = urllib.request.Request(url, data=raw, method='POST', headers={'Content-Type': content_type})
        with urllib.request.urlopen(upstream, timeout=600) as resp:
            stream.bind_response(resp)
            if stream.cancel.is_set():
                _put(('done', None))
                return
            _put(('media_type', resp.headers.get('Content-Type', 'text/event-stream')))
            while not stream.cancel.is_set():
                line = resp.readline()
                if not line:
                    break
                _put(('chunk', line))
        _put(('done', None))
    except Exception as exc:
        if stream.cancel.is_set():
            _put(('done', None))
        else:
            _put(('error', exc))
    finally:
        stream.close()
        _unregister_upstream(stream)


async def open_upstream_chat_stream(
    url: str,
    raw: bytes,
    *,
    content_type: str = 'application/json',
    server_id: str = '',
) -> tuple[str, AsyncIterator[bytes], Callable[[], None]]:
    """Open upstream SSE. Returns (media_type, chunks, close_fn).

    Always call close_fn() when the downstream client disconnects/cancels so
    llama-server stops generating immediately.
    """
    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    stream = UpstreamChatStream(server_id=server_id)
    _register_upstream(stream)
    threading.Thread(
        target=_stream_worker,
        args=(url, raw, content_type, loop, out_q, stream),
        daemon=True,
        name='chat-proxy-stream',
    ).start()

    kind, payload = await out_q.get()
    if kind == 'error':
        stream.close()
        _unregister_upstream(stream)
        raise payload
    if kind == 'done':
        stream.close()
        _unregister_upstream(stream)
        raise RuntimeError('upstream stream closed before media type')
    if kind != 'media_type':
        stream.close()
        _unregister_upstream(stream)
        raise RuntimeError(f'unexpected stream opener event: {kind}')
    media_type = str(payload or 'text/event-stream')

    async def generate() -> AsyncIterator[bytes]:
        try:
            while True:
                try:
                    event_kind, event_payload = await asyncio.wait_for(out_q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # Drain already-queued chunks even if the worker closed first.
                    if stream.cancel.is_set() and out_q.empty():
                        break
                    continue
                if event_kind == 'chunk':
                    yield event_payload
                elif event_kind == 'done':
                    break
                elif event_kind == 'error':
                    raise event_payload
        finally:
            stream.close()
            _unregister_upstream(stream)

    return media_type, generate(), stream.close
