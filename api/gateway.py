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
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from core.config import is_embedding_server, list_servers, load_config

gateway_app = FastAPI(title='DFlash Console OpenAI Gateway', version='0.1.0')

_FORWARD_HEADERS = {'content-type', 'accept', 'authorization'}


def _console_base(cfg: dict[str, Any]) -> str:
    return f"http://127.0.0.1:{int(cfg.get('ui_port') or 8900)}"


def _enabled_chat_servers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in list_servers(cfg) if s.get('enabled', True) and not is_embedding_server(s)]


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
    data: list[dict[str, Any]] = []
    for server in list_servers(cfg):
        if server.get('enabled', True) is False:
            continue
        data.append({
            'id': str(server.get('id') or ''),
            'object': 'model',
            'created': 0,
            'owned_by': 'dflash-console',
            'meta': {
                'engine': str(server.get('label') or server.get('id') or ''),
                'embedding': is_embedding_server(server),
                'model_id': str(server.get('model_id') or ''),
                'api_url': str(server.get('api_url') or ''),
            },
        })
    return {'object': 'list', 'data': data}


async def _forward_chat(request: Request, url: str, body: bytes | None = None) -> Response:
    body = body if body is not None else await request.body()
    headers = _pick_headers(request)
    # Always stream upstream so SSE /v1/chat/completions stays incremental.
    async def stream() -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream('POST', url, content=body, headers=headers) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk

    # Peek whether the caller wants streaming to pick the right content-type.
    want_stream = False
    try:
        payload = await request.json()
        want_stream = bool(payload.get('stream'))
    except Exception:
        pass
    if want_stream:
        return StreamingResponse(stream(), media_type='text/event-stream')
    # Buffered path: read the full body so we can return status + content-type.
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
    url = f"{_console_base(cfg)}/api/servers/{sid}/v1/chat/completions"
    return await _forward_chat(request, url, body)


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
