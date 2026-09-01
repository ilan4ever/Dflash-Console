"""Console OpenAI gateway — one friendly OpenAI-compatible port.

Listens on ``gateway_port`` (default **8001**) and proxies to the Console's own
proxies on the UI port (default 8900). This gives any OpenAI-compatible app a
single stable base URL regardless of which engine is currently loaded::

    base_url = http://127.0.0.1:8001/v1

Routes:
    GET  /v1/models               aggregated model list
    POST /v1/chat/completions     chat on the default engine (streaming)
    POST /v1/embeddings           embeddings on the default embedding engine
    POST /v1/audio/speech         Piper TTS (WAV)
    POST /v1/audio/transcriptions Whisper STT (multipart)
    GET  /health                  gateway + console health
    GET  /                        info

The default chat engine is ``config.json -> gateway_server_id`` (falls back to
the first enabled non-embedding engine); embeddings route to the first enabled
embedding engine. The gateway is started on the UI process lifespan, so it is
stopped and started together with the console.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from core.chat_proxy import wants_stream
from core.config import is_embedding_server, list_runtimes, list_servers, load_config, normalize_inference_settings
from core.local_models import model_has_reasoning

logger = logging.getLogger(__name__)

gateway_app = FastAPI(title='DFlash Console OpenAI Gateway', version='0.1.0')

_FORWARD_HEADERS = {'content-type', 'accept', 'authorization', 'x-disable-reasoning'}
_STREAM_HEADERS = {
    'Cache-Control': 'no-cache',
    'X-Accel-Buffering': 'no',
    'Connection': 'keep-alive',
}


def _console_base(cfg: dict[str, Any]) -> str:
    return f"http://127.0.0.1:{int(cfg.get('ui_port') or 8900)}"


def _enabled_chat_servers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    servers = [s for s in list_servers(cfg) if s.get('enabled', True) and not is_embedding_server(s)]
    known = {str(row.get('id') or '') for row in servers}
    # Adapter engines use the same stable gateway namespace as llama servers.
    # They remain selectable while stopped so the API can return a useful
    # "load a model first" response instead of silently falling back to GGUF.
    for runtime in list_runtimes(cfg):
        runtime_id = str(runtime.get('runtime_id') or '').strip()
        if runtime.get('enabled', True) is False or runtime_id not in {'vllm', 'transformers', 'freetoken'}:
            continue
        if runtime_id in known:
            continue
        servers.append({
            **runtime,
            'id': runtime_id,
            'runtime_id': runtime_id,
            'model_id': str(runtime.get('default_model') or ''),
        })
    return servers


def _chat_server(cfg: dict[str, Any]) -> dict[str, Any]:
    servers = _enabled_chat_servers(cfg)
    if not servers:
        raise HTTPException(status_code=503, detail='no enabled chat engine available')
    wanted = str(cfg.get('gateway_server_id') or '')
    if wanted:
        for server in servers:
            if str(server.get('id') or '') == wanted:
                return server
    return servers[0]


def _embed_server(cfg: dict[str, Any]) -> dict[str, Any]:
    servers = [s for s in list_servers(cfg) if s.get('enabled', True) and is_embedding_server(s)]
    if not servers:
        raise HTTPException(status_code=503, detail='no enabled embedding engine available')
    return servers[0]


def _pick_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _FORWARD_HEADERS
    }


@gateway_app.get('/')
async def index() -> dict[str, Any]:
    cfg = load_config()
    port = int(cfg.get('gateway_port') or 8001)
    return {
        'app': 'DFlash Console OpenAI Gateway',
        'v1': f'http://127.0.0.1:{port}/v1',
        'note': 'Point any OpenAI-compatible client at /v1 (chat, embeddings, audio).',
        'models': f'http://127.0.0.1:{port}/v1/models',
        'health': f'http://127.0.0.1:{port}/health',
    }


@gateway_app.get('/health')
async def health() -> dict[str, Any]:
    cfg = load_config()
    console_ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_console_base(cfg)}/api/health")
            console_ok = resp.status_code == 200
    except Exception:
        console_ok = False
    return {
        'ok': console_ok,
        'gateway_port': int(cfg.get('gateway_port') or 8001),
        'console': _console_base(cfg),
        'stream_reasoning_filter': 'opt_in',
    }


async def _console_servers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Fresh server list from the console (includes active/loaded model ids)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_console_base(cfg)}/api/servers")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data.get('servers') or []
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []


async def _resolve_chat_target(cfg: dict[str, Any], model: str) -> tuple[dict[str, Any], str]:
    """Pick the chat engine for an arbitrary client ``model`` name and return the
    upstream model id the engine will accept (LM-Studio-style tolerance: the
    caller may send the server id, the real model id, or any alias)."""
    servers = _enabled_chat_servers(cfg)
    if not servers:
        raise HTTPException(status_code=503, detail='no enabled chat engine available')
    model = (model or '').strip()
    target: dict[str, Any] | None = None
    if model:
        for server in servers:
            if str(server.get('id') or '') == model:
                target = server
                break
    if target is None and model:
        for server in servers:
            if str(server.get('model_id') or '') == model:
                target = server
                break
    if target is None:
        wanted = str(cfg.get('gateway_server_id') or '')
        if wanted:
            for server in servers:
                if str(server.get('id') or '') == wanted:
                    target = server
                    break
        if target is None:
            target = servers[0]
    live: dict[str, Any] = {}
    sid = str(target.get('id') or '')
    for entry in await _console_servers(cfg):
        if str(entry.get('id') or '') == sid:
            live = entry
            break
    loaded = live.get('loaded_models') or []
    upstream = (
        str(live.get('active_model_id') or '')
        or (str(loaded[0]) if loaded else '')
        or str(target.get('model_id') or '')
    )
    return target, upstream


@gateway_app.get('/v1/models')
async def list_models() -> dict[str, Any]:
    cfg = load_config()
    from core.display_names import build_engine_client_metadata
    from core.model_stack import resolve_model_stack

    data: list[dict[str, Any]] = []
    for server in list_servers(cfg):
        if server.get('enabled', True) is False:
            continue
        infer = normalize_inference_settings(server.get('inference_settings'))
        try:
            stack = resolve_model_stack(server, cfg=cfg)
        except ValueError:
            stack = []
        client_meta = build_engine_client_metadata(server, stack)
        display_name = str(
            client_meta.get('display_name_full')
            or client_meta.get('display_name')
            or server.get('label')
            or server.get('id')
            or '',
        ).strip()
        api_model_id = str(server.get('model_id') or '')
        data.append({
            'id': str(server.get('id') or ''),
            'object': 'model',
            'created': 0,
            'owned_by': 'dflash-console',
            'name': display_name,
            'meta': {
                'engine': display_name,
                'display_name': display_name,
                'api_model_id': api_model_id,
                'embedding': is_embedding_server(server),
                'model_id': api_model_id,
                'engine_mode': (client_meta.get('model_catalog') or {}).get('engine_mode') or '',
                'api_url': str(server.get('api_url') or ''),
                'reasoning': model_has_reasoning(server),
                'reasoning_effort': str(infer.get('reasoning_effort') or 'auto'),
            },
        })
    from core.runtimes import get_runtime_adapter

    for runtime in list_runtimes(cfg):
        runtime_id = str(runtime.get('runtime_id') or '').strip()
        if runtime.get('enabled', True) is False or runtime_id not in {'vllm', 'transformers', 'freetoken'}:
            continue
        adapter = get_runtime_adapter(runtime_id)
        health = adapter.health() if adapter is not None else {}
        active = str(health.get('active_model') or runtime.get('default_model') or '').strip()
        if not active:
            continue
        data.append({
            'id': runtime_id,
            'object': 'model',
            'created': 0,
            'owned_by': 'dflash-console',
            'name': active.rsplit('/', 1)[-1].rsplit('\\', 1)[-1],
            'meta': {
                'engine': str(runtime.get('label') or runtime_id),
                'display_name': str(runtime.get('label') or runtime_id),
                'api_model_id': active,
                'runtime_id': runtime_id,
                'api_url': str(health.get('api_url') or ''),
                'running': health.get('running') is True,
            },
        })
    return {'object': 'list', 'data': data}


async def _forward_chat(
    request: Request,
    url: str,
    body: bytes | None = None,
    *,
    filter_reasoning: bool = False,
) -> Response:
    body = body if body is not None else await request.body()
    headers = _pick_headers(request)
    if filter_reasoning:
        headers['x-disable-reasoning'] = '1'
    # The request body may already have been consumed by the route handler.
    # Always derive stream intent from the forwarded bytes, not request.json().
    stream_requested = wants_stream(body)

    async def stream() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream('POST', url, content=body, headers=headers) as upstream:
                    upstream.raise_for_status()
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except httpx.HTTPStatusError as exc:
            logger.warning('gateway chat upstream HTTP %s for %s', exc.response.status_code, url)
            try:
                detail = (await exc.response.aread()).decode('utf-8', errors='replace')
            except Exception:
                detail = str(exc)
            if stream_requested:
                payload = json.dumps({'error': {'message': detail, 'type': 'upstream_error'}})
                yield f'data: {payload}\n\n'.encode('utf-8')
                yield b'data: [DONE]\n\n'
            else:
                yield detail.encode('utf-8', errors='replace')
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.WriteError) as exc:
            logger.warning('gateway chat stream drop for %s: %s', url, exc)
            if stream_requested:
                payload = json.dumps({'error': {'message': str(exc), 'type': 'stream_error'}})
                yield f'data: {payload}\n\n'.encode('utf-8')
                yield b'data: [DONE]\n\n'
            else:
                yield json.dumps({'error': {'message': str(exc), 'type': 'stream_error'}}).encode('utf-8')

    if stream_requested:
        return StreamingResponse(stream(), media_type='text/event-stream', headers=_STREAM_HEADERS)
    async with httpx.AsyncClient(timeout=None) as client:
        upstream = await client.post(url, content=body, headers=headers)
        content = upstream.content
        media_type = upstream.headers.get('content-type', 'application/json')
        status = upstream.status_code
    if status >= 400:
        return Response(content=content, status_code=status, media_type=media_type)
    return Response(content=content, status_code=status, media_type=media_type)


@gateway_app.post('/v1/chat/completions')
async def chat_completions(request: Request) -> Response:
    cfg = load_config()
    model = ''
    payload: Any = None
    try:
        payload = await request.json()
    except Exception:
        pass
    if isinstance(payload, dict):
        model = payload.get('model')
        if not isinstance(model, str):
            model = ''
    server, upstream = await _resolve_chat_target(cfg, model)
    sid = str(server.get('id') or '')
    body: bytes | None = None
    if isinstance(payload, dict):
        try:
            if upstream:
                payload['model'] = upstream
            body = json.dumps(payload).encode('utf-8')
        except Exception:
            body = None
    if body is not None:
        from core.chat_proxy import apply_reasoning_policy

        body = apply_reasoning_policy(body, reasoning=model_has_reasoning(server))
    url = f"{_console_base(cfg)}/api/servers/{sid}/v1/chat/completions"
    # Hide reasoning-only SSE chunks only when the client opts in. Agent
    # clients (Hermes, tool loops) need reasoning_content by default.
    filter_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
    return await _forward_chat(request, url, body, filter_reasoning=filter_reasoning)


@gateway_app.post('/v1/embeddings')
async def embeddings(request: Request) -> Response:
    cfg = load_config()
    server = _embed_server(cfg)
    sid = str(server.get('id') or '')
    url = f"{_console_base(cfg)}/api/servers/{sid}/v1/embeddings"
    return await _forward_chat(request, url)


@gateway_app.post('/v1/audio/speech')
async def audio_speech(request: Request) -> Response:
    cfg = load_config()
    url = f"{_console_base(cfg)}/api/runtimes/piper/v1/audio/speech"
    return await _forward_chat(request, url)


@gateway_app.post('/v1/audio/transcriptions')
async def audio_transcriptions(request: Request) -> Response:
    cfg = load_config()
    url = f"{_console_base(cfg)}/api/runtimes/stt/v1/audio/transcriptions"
    return await _forward_chat(request, url)
