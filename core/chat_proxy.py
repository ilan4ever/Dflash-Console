"""Proxy chat/completions to llama-server (JSON and SSE streaming)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Per-read socket timeout while draining an upstream SSE stream. Each readline()
# must complete within this window; long gaps without tokens (e.g. reasoning)
# need Console-side SSE keep-alives so clients do not think the hop died.
DEFAULT_CHAT_UPSTREAM_READ_TIMEOUT = 3600.0
SSE_KEEPALIVE_COMMENT = b': keep-alive\n\n'


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


def estimate_request_context(body: dict[str, Any], *, default_output: int = 4096) -> int:
    """Estimate the context window (in tokens) a chat request needs.

    Returns input_tokens + output_tokens + a safety margin.  The Console uses
    this to decide whether a request fits the currently-loaded model context or
    whether the model should be reloaded with a larger context (auto-grow).
    """
    if not isinstance(body, dict):
        return 0
    total_chars = 0
    messages = body.get('messages')
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get('content')
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    text = part.get('text')
                    if isinstance(text, str):
                        total_chars += len(text)
                    elif isinstance(part.get('content'), str):
                        total_chars += len(part['content'])
    # ~3 chars/token is a conservative estimate for mixed prose+code, so we
    # tend to over-estimate and avoid overflow (the goal of auto-grow).
    input_tokens = max(1, total_chars // 3)
    try:
        max_tokens = int(body.get('max_tokens') or 0)
    except (TypeError, ValueError):
        max_tokens = 0
    if max_tokens <= 0:
        max_tokens = default_output
    return input_tokens + max_tokens + 512


def chat_upstream_read_timeout(cfg: dict[str, Any] | None = None) -> float:
    """Return the upstream per-read timeout (seconds) for chat SSE streams."""
    if isinstance(cfg, dict):
        try:
            value = float(cfg.get('chat_upstream_read_timeout_seconds') or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return DEFAULT_CHAT_UPSTREAM_READ_TIMEOUT


def prepare_upstream_stream_body(raw: bytes) -> bytes:
    """Force OpenAI-style streaming + usage on every upstream chat request."""
    body = parse_chat_body(raw)
    body['stream'] = True
    stream_options = body.get('stream_options')
    if not isinstance(stream_options, dict):
        stream_options = {}
    stream_options.setdefault('include_usage', True)
    body['stream_options'] = stream_options
    return json.dumps(body).encode('utf-8')


def sse_stream_complete(raw: bytes) -> bool:
    """Return True when an SSE buffer ends with an explicit ``[DONE]`` marker."""
    if not raw:
        return False
    for line in raw.decode('utf-8', errors='replace').splitlines():
        payload = line[5:].strip() if line.startswith('data:') else line.strip()
        if payload == '[DONE]':
            return True
    return False


def sse_had_content_delta(raw: bytes) -> bool:
    """Return True when any SSE chunk carried a non-empty assistant content delta."""
    if not raw:
        return False
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
        if not isinstance(parsed, dict):
            continue
        choices = parsed.get('choices')
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get('delta') if isinstance(choice.get('delta'), dict) else {}
        message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
        piece = delta.get('content')
        if piece is None:
            piece = message.get('content')
        if isinstance(piece, str) and piece:
            return True
    return False


def empty_completion_guard(payload: dict[str, Any]) -> str | None:
    """Return a client-facing error when a completion has no assistant content."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get('choices')
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
    content = message.get('content')
    if isinstance(content, str) and content.strip():
        return None
    reasoning = message.get('reasoning_content')
    finish_reason = str(choice.get('finish_reason') or '')
    if isinstance(reasoning, str) and reasoning.strip():
        if finish_reason == 'length':
            return (
                'Model stopped at max_tokens while still in reasoning. '
                'Set reasoning_effort to "none", send X-Disable-Reasoning: 1, '
                'or raise max_tokens.'
            )
        return (
            'Model produced reasoning only. '
            'Set reasoning_effort to "none", send X-Disable-Reasoning: 1, '
            'or raise max_tokens.'
        )
    if finish_reason == 'length':
        return 'Model stopped at max_tokens before emitting assistant content.'
    return None


def sse_stream_error_chunk(message: str) -> bytes:
    """Emit a terminal OpenAI-style SSE error frame."""
    body = json.dumps({
        'error': {
            'message': message,
            'type': 'stream_error',
        },
    })
    return f'data: {body}\n\n'.encode('utf-8') + b'data: [DONE]\n\n'


def apply_reasoning_policy(
    raw: bytes,
    *,
    reasoning: bool,
    disable_reasoning: bool = False,
) -> bytes:
    """Rewrite a chat request body's reasoning controls for the target model.

    Non-reasoning models must behave like a plain chat model: drop
    ``reasoning_effort`` (and any thinking toggles) so the API never asks for
    reasoning and the engine keeps its regular output.

    Reasoning models keep the client's ``reasoning_effort`` unless the caller
    disables reasoning via ``X-Disable-Reasoning: 1`` or ``reasoning_effort:
    none`` — in that case we forward ``reasoning_effort: none`` upstream so the
    engine skips the thinking phase instead of burning the token budget.
    """
    if not raw:
        return raw
    try:
        body = json.loads(raw.decode('utf-8', errors='replace'))
    except json.JSONDecodeError:
        return raw
    if not isinstance(body, dict):
        return raw
    changed = False
    if reasoning:
        effort = str(body.get('reasoning_effort') or '').strip().lower()
        if disable_reasoning or effort == 'none':
            if body.get('reasoning_effort') != 'none':
                body['reasoning_effort'] = 'none'
                changed = True
            for key in ('thinking', 'enable_thinking'):
                if key in body:
                    body.pop(key, None)
                    changed = True
            try:
                max_tokens = int(body.get('max_tokens') or 0)
            except (TypeError, ValueError):
                max_tokens = 0
            if 0 < max_tokens < 64:
                body['max_tokens'] = 64
                changed = True
        if not changed:
            return raw
        try:
            return json.dumps(body).encode('utf-8')
        except (TypeError, ValueError):
            return raw
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


def reasoning_disabled_for_request(raw: bytes, *, disable_header: bool = False) -> bool:
    """True when the client asked to skip the reasoning phase."""
    if disable_header:
        return True
    body = parse_chat_body(raw)
    return str(body.get('reasoning_effort') or '').strip().lower() == 'none'


def validate_reasoning_chat_request(
    raw: bytes,
    *,
    reasoning: bool,
    disable_reasoning: bool,
) -> str | None:
    """Return a client-facing 400 hint when reasoning would exhaust max_tokens."""
    if not reasoning or reasoning_disabled_for_request(raw, disable_header=disable_reasoning):
        return None
    body = parse_chat_body(raw)
    try:
        max_tokens = int(body.get('max_tokens') or 0)
    except (TypeError, ValueError):
        max_tokens = 0
    if max_tokens <= 0 or max_tokens >= 128:
        return None
    return (
        'Reasoning models need room for a thinking phase. '
        'Set reasoning_effort to "none", send header X-Disable-Reasoning: 1, '
        'or raise max_tokens to at least 128.'
    )


def aggregate_sse_to_completion(raw: bytes) -> dict[str, Any]:
    """Fold an OpenAI-style SSE chat stream into one completion JSON object."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    model: str | None = None
    if not raw:
        return {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': ''}, 'finish_reason': 'stop'}]}
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
        if not isinstance(parsed, dict):
            continue
        if isinstance(parsed.get('model'), str):
            model = parsed['model']
        choices = parsed.get('choices')
        if isinstance(choices, list) and choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            delta = choice.get('delta') if isinstance(choice.get('delta'), dict) else {}
            message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
            piece = delta.get('content')
            if piece is None:
                piece = message.get('content')
            if isinstance(piece, str) and piece:
                content_parts.append(piece)
            reasoning = delta.get('reasoning_content')
            if reasoning is None:
                reasoning = message.get('reasoning_content')
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
            if isinstance(choice.get('finish_reason'), str):
                finish_reason = choice['finish_reason']
        if isinstance(parsed.get('usage'), dict):
            usage = parsed['usage']
    message: dict[str, Any] = {'role': 'assistant', 'content': ''.join(content_parts)}
    if reasoning_parts:
        message['reasoning_content'] = ''.join(reasoning_parts)
    result: dict[str, Any] = {
        'choices': [{
            'index': 0,
            'message': message,
            'finish_reason': finish_reason or 'stop',
        }],
    }
    if model:
        result['model'] = model
    if usage:
        result['usage'] = usage
    return result


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


# SSE data lines that are pure reasoning (no content delta).  We drop these
# so that clients that do not understand ``reasoning_content`` (e.g. the VS Code
# custom-LLM-provider extension) receive regular ``content`` deltas without a
# long silent wait while the model thinks.
_REASONING_ONLY_LINE_PATTERNS = (
    b'"reasoning_content":',
    b'"reasoning_details":',
)


def is_reasoning_only_chunk(chunk: bytes) -> bool:
    """Return True when *chunk* is an SSE data line that carries only reasoning.

    Keeps the initial ``delta: {"role":"assistant"}`` chunk (so the client
    knows the turn started), usage chunks, ``[DONE]`` markers, and any chunk
    that contains a real ``content`` delta.
    """
    if not chunk.startswith(b'data: '):
        return False
    payload = chunk[5:]
    if not payload or payload == b'[DONE]' or payload == b'\n':
        return False
    # Keep the initial role delta (content is always null there).
    if b'"delta":{"role":"assistant"' in payload:
        return False
    # Keep any chunk that carries a content delta.
    if b'"content":' in payload:
        return False
    for pat in _REASONING_ONLY_LINE_PATTERNS:
        if pat in payload:
            return True
    return False


def _upstream_stream_raw(
    url: str,
    raw: bytes,
    *,
    content_type: str = 'application/json',
    extra_headers: dict[str, str] | None = None,
    server_id: str = '',
    read_timeout: float | None = None,
) -> tuple[int, bytes | dict[str, Any]]:
    """Open a cancellable upstream SSE chat stream and return the raw SSE bytes."""
    upstream_raw = prepare_upstream_stream_body(raw)
    timeout = float(read_timeout or DEFAULT_CHAT_UPSTREAM_READ_TIMEOUT)

    headers = {'Content-Type': content_type}
    if extra_headers:
        headers.update({str(k): str(v) for k, v in extra_headers.items()})
    upstream = urllib.request.Request(url, data=upstream_raw, method='POST', headers=headers)
    stream = UpstreamChatStream(server_id)
    _register_upstream(stream)
    buffer = bytearray()
    try:
        resp = urllib.request.urlopen(upstream, timeout=timeout)
        stream.bind_response(resp)
        if stream.cancel.is_set():
            return 499, {'error': 'inference cancelled'}
        try:
            while not stream.cancel.is_set():
                line = resp.readline()
                if not line:
                    break
                buffer.extend(line)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if stream.cancel.is_set():
            return 499, {'error': 'inference cancelled'}
        payload = bytes(buffer)
        if payload and not sse_stream_complete(payload):
            logger.warning(
                'upstream chat stream closed before [DONE] server=%s url=%s bytes=%d',
                server_id or '-',
                url,
                len(payload),
            )
        return int(getattr(resp, 'status', 200) or 200), payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        try:
            body_json = json.loads(detail)
        except json.JSONDecodeError:
            body_json = {'error': detail or str(exc)}
        return exc.code, body_json if isinstance(body_json, dict) else {'error': str(body_json)}
    finally:
        stream.close()
        _unregister_upstream(stream)


def upstream_chat_completion(
    url: str,
    raw: bytes,
    *,
    content_type: str = 'application/json',
    extra_headers: dict[str, str] | None = None,
    server_id: str = '',
) -> tuple[int, dict[str, Any]]:
    """Blocking chat/completions proxy — upstream always streams so Stop closes llama."""
    status, payload = _upstream_stream_raw(
        url,
        raw,
        content_type=content_type,
        extra_headers=extra_headers,
        server_id=server_id,
    )
    if isinstance(payload, dict):
        return status, payload
    if status >= 400:
        try:
            parsed = json.loads(payload.decode('utf-8', errors='replace') or '{}')
        except json.JSONDecodeError:
            parsed = {'error': payload.decode('utf-8', errors='replace')[:500]}
        return status, parsed if isinstance(parsed, dict) else {'error': str(parsed)}
    return status, aggregate_sse_to_completion(payload)


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
    extra_headers: dict[str, str] | None = None,
    read_timeout: float | None = None,
) -> None:
    def _put(item: tuple[str, Any]) -> None:
        if stream.cancel.is_set():
            return
        try:
            loop.call_soon_threadsafe(out_q.put_nowait, item)
        except RuntimeError:
            # Event loop already closed after client disconnect.
            pass

    saw_done = False
    timeout = float(read_timeout or DEFAULT_CHAT_UPSTREAM_READ_TIMEOUT)
    try:
        headers = {'Content-Type': content_type}
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        upstream_raw = prepare_upstream_stream_body(raw)
        upstream = urllib.request.Request(url, data=upstream_raw, method='POST', headers=headers)
        with urllib.request.urlopen(upstream, timeout=timeout) as resp:
            stream.bind_response(resp)
            if stream.cancel.is_set():
                _put(('done', True))
                return
            _put(('media_type', resp.headers.get('Content-Type', 'text/event-stream')))
            while not stream.cancel.is_set():
                line = resp.readline()
                if not line:
                    break
                payload = line.strip()
                if payload == b'data: [DONE]' or payload.endswith(b'[DONE]'):
                    saw_done = True
                _put(('chunk', line))
        if not saw_done and not stream.cancel.is_set():
            logger.warning(
                'upstream chat stream worker closed before [DONE] server=%s url=%s',
                stream.server_id or '-',
                url,
            )
        _put(('done', saw_done))
    except Exception as exc:
        if stream.cancel.is_set():
            _put(('done', True))
        else:
            logger.warning(
                'upstream chat stream worker failed server=%s url=%s: %s',
                stream.server_id or '-',
                url,
                exc,
            )
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
    extra_headers: dict[str, str] | None = None,
    read_timeout: float | None = None,
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
        args=(url, raw, content_type, loop, out_q, stream, extra_headers, read_timeout),
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
                    if event_payload is False:
                        yield sse_stream_error_chunk('Upstream chat stream closed before completion')
                    break
                elif event_kind == 'error':
                    yield sse_stream_error_chunk(str(event_payload))
                    break
        finally:
            stream.close()
            _unregister_upstream(stream)

    return media_type, generate(), stream.close
