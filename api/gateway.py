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

Concurrent OpenAI clients on the same loaded engine (for example DeepSeek Harness
main turn + session title) are accepted: the Console queues the ready/start
section per ``server_id``, then llama-server multiplexes within ``parallel_slots``.
Steady-state overlaps must not return HTTP 409. Intentional 409s remain for
``X-DFlash-Strict-Model`` mismatches, duplicate checkpoint on another engine,
DFlash stack / vision repair, and load/unload conflicts.

The default chat engine is ``config.json -> gateway_server_id`` (falls back to
the first enabled non-embedding engine); embeddings route to the first enabled
embedding engine. The gateway is started on the UI process lifespan, so it is
stopped and started together with the console.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from core.chat_proxy import wants_stream
from core.config import is_embedding_server, list_runtimes, list_servers, load_config, normalize_inference_settings
from core.gateway_routing import (
    CURSOR_COMPAT_MODEL_IDS,
    catalog_model_id,
    default_gateway_chat_server,
    enabled_chat_servers,
    gateway_model_aliases,
    gateway_public_model_ids,
    is_cursor_compat_model_id,
    resolve_chat_server,
    _advertised_engine_server,
)
from core.local_models import model_has_reasoning
from core.model_presets import server_target_path_on_disk

logger = logging.getLogger(__name__)

gateway_app = FastAPI(title='DFlash Console OpenAI Gateway', version='0.1.0')


def gateway_cloud_chat_enabled(cfg: dict[str, Any]) -> bool:
    """Allow active Settings cloud models unless explicitly disabled."""
    return cfg.get('gateway_cloud_chat_enabled') is not False

_FORWARD_HEADERS = {
    'content-type',
    'accept',
    'authorization',
    'x-disable-reasoning',
    'x-dflash-client',
    'x-dflash-load-context',
    'user-agent',
    'referer',
}
_STREAM_HEADERS = {
    'Cache-Control': 'no-cache',
    'X-Accel-Buffering': 'no',
    'Connection': 'keep-alive',
}


def _upstream_error_payload(raw: bytes, status_code: int) -> dict[str, Any]:
    """Normalize Console/FastAPI error bodies for gateway clients (Harness, OpenAI SDK)."""
    text = raw.decode('utf-8', errors='replace').strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {
            'error': {
                'message': text or f'upstream HTTP {status_code}',
                'type': 'upstream_error',
                'code': status_code,
            }
        }
    if not isinstance(parsed, dict):
        return {
            'error': {
                'message': text,
                'type': 'upstream_error',
                'code': status_code,
            }
        }
    detail = parsed.get('detail')
    if isinstance(detail, dict):
        nested = detail.get('error')
        if isinstance(nested, dict):
            return {'error': nested}
        if isinstance(nested, str):
            return {
                'error': {
                    'message': str(detail.get('message') or nested),
                    'type': 'invalid_request_error',
                    'code': status_code,
                    'reason': nested,
                    **{k: v for k, v in detail.items() if k not in {'error', 'message'}},
                }
            }
        return {'error': detail}
    if isinstance(parsed.get('error'), dict):
        return parsed
    return {
        'error': {
            'message': text,
            'type': 'upstream_error',
            'code': status_code,
        }
    }


def _console_base(cfg: dict[str, Any]) -> str:
    return f"http://127.0.0.1:{int(cfg.get('ui_port') or 8900)}"


def _enabled_chat_servers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return enabled_chat_servers(cfg)


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


def _gateway_lists_server(server: dict[str, Any], *, cfg: dict[str, Any]) -> bool:
    """Advertise engine profiles whose target checkpoint exists on disk."""
    return _advertised_engine_server(server, cfg=cfg)


async def _resolve_chat_target(cfg: dict[str, Any], model: str) -> tuple[dict[str, Any], str]:
    """Pick the chat engine for an arbitrary client ``model`` name."""
    target = resolve_chat_server(cfg, model)
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


def _require_pipeline_active() -> None:
    cfg = load_config()
    from core.engine_state import console_pipeline_active, engine_standby_http_error

    if not console_pipeline_active(cfg):
        raise HTTPException(status_code=503, detail=engine_standby_http_error())


@gateway_app.get('/v1/models')
async def list_models() -> dict[str, Any]:
    cfg = load_config()
    from core.display_names import build_engine_client_metadata, disambiguate_engine_display_names
    from core.model_stack import resolve_model_stack
    from core.vision_setup import resolve_mmproj_path, server_supports_vision_chat

    pending: list[dict[str, Any]] = []
    seen_model_ids: set[str] = set()
    for server in list_servers(cfg):
        if not _gateway_lists_server(server, cfg=cfg):
            continue
        client_id = catalog_model_id(server)
        if client_id in seen_model_ids:
            continue
        seen_model_ids.add(client_id)
        infer = normalize_inference_settings(server.get('inference_settings'))
        try:
            stack = resolve_model_stack(server, cfg=cfg)
        except ValueError:
            stack = []
        client_meta = build_engine_client_metadata(server, stack)
        pending.append({
            **client_meta,
            'id': str(server.get('id') or ''),
            'model_id': str(server.get('model_id') or ''),
            'load_settings': server.get('load_settings'),
            '_server': server,
            '_infer': infer,
            '_client_id': client_id,
            '_stack': stack,
        })
    disambiguate_engine_display_names(pending)

    data: list[dict[str, Any]] = []
    for item in pending:
        server = item['_server']
        infer = item['_infer']
        client_id = item['_client_id']
        display_name = str(
            item.get('display_name_full')
            or item.get('display_name')
            or server.get('label')
            or server.get('id')
            or '',
        ).strip()
        api_model_id = str(server.get('model_id') or '')
        mmproj_path = str(resolve_mmproj_path(server, cfg=cfg) or '').strip()
        supports_vision = server_supports_vision_chat(server, cfg=cfg)
        context_size = max(2048, int(server.get('context_size') or 8192))
        catalog = item.get('model_catalog') if isinstance(item.get('model_catalog'), dict) else {}
        from core.model_presets import (
            effective_server_profile,
            profile_requires_draft,
            server_dflash_stack_ready,
        )

        configured_profile = str(server.get('profile') or '').strip()
        draft_ready = server_dflash_stack_ready(server, cfg=cfg)
        effective = effective_server_profile(server, cfg=cfg)
        requires_draft_for_load = profile_requires_draft(effective)
        base_meta = {
            'engine': display_name,
            'display_name': display_name,
            'server_id': str(server.get('id') or ''),
            'api_model_id': api_model_id,
            'aliases': sorted(gateway_model_aliases(server, cfg=cfg)),
            'embedding': is_embedding_server(server),
            'model_id': api_model_id,
            'engine_mode': catalog.get('engine_mode') or '',
            'api_url': str(server.get('api_url') or ''),
            'reasoning': model_has_reasoning(server),
            'reasoning_effort': str(infer.get('reasoning_effort') or 'auto'),
            'supports_vision': supports_vision,
            'imageInput': supports_vision,
            'mmproj_path': mmproj_path if supports_vision else '',
            'context_size': context_size,
            'configured_profile': configured_profile,
            'draft_required': profile_requires_draft(configured_profile),
            'dflash_ready': draft_ready,
            'loadable': bool(server_target_path_on_disk(server, cfg=cfg))
            and (not requires_draft_for_load or draft_ready),
            'enabled': server.get('enabled', True) is not False,
            'canonical_id': client_id,
        }
        for public_id in gateway_public_model_ids(server, cfg=cfg):
            row_meta = dict(base_meta)
            if public_id != client_id:
                row_meta['alias_of'] = client_id
            data.append({
                'id': public_id,
                'object': 'model',
                'created': 0,
                'owned_by': 'dflash-console',
                'name': display_name,
                'context_size': context_size,
                'meta': row_meta,
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
    listed_ids = {str(row.get('id') or '').lower() for row in data}
    try:
        default_server = default_gateway_chat_server(cfg)
        canonical = catalog_model_id(default_server)
        default_label = str(default_server.get('label') or default_server.get('id') or canonical)
        for alias in sorted(CURSOR_COMPAT_MODEL_IDS):
            if alias in listed_ids:
                continue
            data.append({
                'id': alias,
                'object': 'model',
                'created': 0,
                'owned_by': 'dflash-console',
                'name': default_label,
                'context_size': max(2048, int(default_server.get('context_size') or 8192)),
                'meta': {
                    'engine': default_label,
                    'display_name': default_label,
                    'server_id': str(default_server.get('id') or ''),
                    'canonical_id': canonical,
                    'alias_of': canonical,
                    'cursor_compat': True,
                    'note': 'Cursor IDE accepts this id; routes to the default gateway chat engine.',
                },
            })
            listed_ids.add(alias)
    except HTTPException:
        pass
    from core.api_providers import cloud_model_entries, with_api_label

    if not gateway_cloud_chat_enabled(cfg):
        return {'object': 'list', 'data': data}

    for entry in cloud_model_entries(cfg):
        mid = str(entry.get('id') or '').strip()
        if not mid or mid.lower() in listed_ids:
            continue
        provider_label = str(entry.get('provider_label') or entry.get('provider_id') or 'cloud')
        model_label = str(entry.get('label') or mid).strip() or mid
        public_name = with_api_label(model_label)
        owned_by = with_api_label(provider_label)
        data.append({
            'id': mid,
            'object': 'model',
            'created': 0,
            'owned_by': owned_by,
            'name': public_name,
            'meta': {
                'engine': owned_by,
                'display_name': public_name,
                'provider_id': str(entry.get('provider_id') or ''),
                'provider_label': provider_label,
                'cloud': True,
                'source': 'api',
                'api': True,
                'canonical_id': mid,
                'model_label': model_label,
            },
        })
        listed_ids.add(mid.lower())
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
                    if upstream.status_code >= 400:
                        raw_err = await upstream.aread()
                        # Re-raise as HTTPStatusError with body already buffered.
                        response = httpx.Response(
                            upstream.status_code,
                            content=raw_err,
                            request=upstream.request,
                            headers=upstream.headers,
                        )
                        raise httpx.HTTPStatusError(
                            f'Client error {upstream.status_code}',
                            request=upstream.request,
                            response=response,
                        )
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code or 500)
            logger.warning('gateway chat upstream HTTP %s for %s', status, url)
            try:
                raw = exc.response.content or b''
                if not raw:
                    raw = await exc.response.aread()
            except Exception:
                raw = b''
            try:  # DFLASH_LOG_400_BODIES (stream path)
                from pathlib import Path as _P
                _log = _P(__file__).resolve().parents[1] / 'logs' / 'gateway-400-bodies.log'
                _log.parent.mkdir(parents=True, exist_ok=True)
                with _log.open('a', encoding='utf-8') as _fh:
                    _fh.write(
                        f"stream status={status} url={url} "
                        f"req={body[:2000]!r} resp={raw[:4000]!r}\n"
                    )
            except Exception:
                pass
            error_payload = _upstream_error_payload(raw, status)
            encoded = json.dumps(error_payload).encode('utf-8')
            if stream_requested:
                yield f'data: {encoded.decode("utf-8")}\n\n'.encode('utf-8')
                yield b'data: [DONE]\n\n'
            else:
                yield encoded
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
        try:  # DFLASH_LOG_400_BODIES
            from pathlib import Path as _P
            _log = _P(__file__).resolve().parents[1] / 'logs' / 'gateway-400-bodies.log'
            _log.parent.mkdir(parents=True, exist_ok=True)
            with _log.open('a', encoding='utf-8') as _fh:
                _fh.write(f"status={status} url={url} body={content[:4000]!r}\n")
        except Exception:
            pass
        return Response(content=content, status_code=status, media_type=media_type)
    return Response(content=content, status_code=status, media_type=media_type)


async def _forward_cloud_chat(
    request: Request,
    *,
    provider: dict[str, Any],
    body: bytes,
) -> Response:
    """Proxy chat completions to an external OpenAI-compatible provider."""
    from core.api_providers import chat_completions_url, resolve_provider_api_key

    api_key = resolve_provider_api_key(provider)
    if not api_key:
        raise HTTPException(status_code=401, detail='cloud provider API key is not configured')
    url = chat_completions_url(provider)
    provider_id = str(provider.get('id') or 'cloud')
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'Accept': request.headers.get('accept') or 'application/json',
    }
    stream_requested = wants_stream(body)
    # Never log api_key or Authorization.
    logger.info('gateway cloud chat -> provider=%s stream=%s', provider_id, stream_requested)

    async def stream() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream('POST', url, content=body, headers=headers) as upstream:
                    if upstream.status_code >= 400:
                        raw = await upstream.aread()
                        error_payload = _upstream_error_payload(raw, int(upstream.status_code))
                        encoded = json.dumps(error_payload).encode('utf-8')
                        if stream_requested:
                            yield f'data: {encoded.decode("utf-8")}\n\n'.encode('utf-8')
                            yield b'data: [DONE]\n\n'
                        else:
                            yield encoded
                        return
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.WriteError) as exc:
            logger.warning('gateway cloud stream drop provider=%s: %s', provider_id, exc)
            if stream_requested:
                payload = json.dumps({'error': {'message': str(exc), 'type': 'stream_error'}})
                yield f'data: {payload}\n\n'.encode('utf-8')
                yield b'data: [DONE]\n\n'
            else:
                yield json.dumps({'error': {'message': str(exc), 'type': 'stream_error'}}).encode('utf-8')

    if stream_requested:
        response = StreamingResponse(stream(), media_type='text/event-stream', headers=_STREAM_HEADERS)
        response.headers['X-DFlash-Provider-Id'] = provider_id
        return response
    async with httpx.AsyncClient(timeout=None) as client:
        upstream = await client.post(url, content=body, headers=headers)
        content = upstream.content
        media_type = upstream.headers.get('content-type', 'application/json')
        status = upstream.status_code
    if status >= 400:
        error_payload = _upstream_error_payload(content, status)
        content = json.dumps(error_payload).encode('utf-8')
        media_type = 'application/json'
    response = Response(content=content, status_code=status, media_type=media_type)
    response.headers['X-DFlash-Provider-Id'] = provider_id
    return response


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
    from core.api_providers import resolve_cloud_provider_for_model
    from core.client_identity import resolve_client_label
    from core.gateway_access_log import record_gateway_route

    # A cloud model selected as the gateway default must also handle clients
    # that omit ``model`` or send a Cursor-compatible placeholder.
    configured_model = str(cfg.get('gateway_server_id') or '').strip()
    if (
        gateway_cloud_chat_enabled(cfg)
        and configured_model
        and (not model or is_cursor_compat_model_id(model))
        and resolve_cloud_provider_for_model(cfg, configured_model) is not None
    ):
        model = configured_model

    client_label = resolve_client_label(request)
    cloud_provider = (
        resolve_cloud_provider_for_model(cfg, model)
        if gateway_cloud_chat_enabled(cfg)
        else None
    )
    if cloud_provider is not None:
        provider_id = str(cloud_provider.get('id') or 'cloud')
        record_gateway_route(
            model=model,
            route='cloud',
            target=provider_id,
            client=client_label,
            note=str(cloud_provider.get('base_url') or ''),
        )
        body: bytes | None = None
        if isinstance(payload, dict):
            payload['model'] = model
            try:
                body = json.dumps(payload).encode('utf-8')
            except Exception:
                body = None
        if body is None:
            body = await request.body()
        response = await _forward_cloud_chat(request, provider=cloud_provider, body=body)
        if isinstance(response, Response):
            response.headers['X-DFlash-Route'] = 'cloud'
            response.headers['X-DFlash-Provider-Id'] = provider_id
        return response
    server, upstream = await _resolve_chat_target(cfg, model)
    sid = str(server.get('id') or '')
    record_gateway_route(
        model=model,
        route='local',
        target=sid or str(server.get('label') or ''),
        client=client_label,
        note=str(upstream or ''),
    )
    if sid:
        from core.engine_state import note_engine_on

        note_engine_on(sid)
    body: bytes | None = None
    if isinstance(payload, dict):
        try:
            if upstream:
                payload['model'] = upstream
            body = json.dumps(payload).encode('utf-8')
        except Exception:
            body = None
    disable_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
    if body is not None:
        from core.chat_proxy import apply_reasoning_policy, resolve_disable_reasoning_for_chat
        from core.client_identity import resolve_client_label, LABEL_UNKNOWN_API

        reasoning_model = model_has_reasoning(server)
        client_label = resolve_client_label(request)
        attributed = bool(client_label) and client_label != LABEL_UNKNOWN_API
        # External OpenAI clients (Cursor, SDKs) use the gateway without X-DFlash-Client.
        # Treat them like attributed clients so low max_tokens disables reasoning instead of 400.
        if not attributed:
            attributed = True
        disable_reasoning, reasoning_error = resolve_disable_reasoning_for_chat(
            body,
            reasoning=reasoning_model,
            disable_header=disable_reasoning,
            attributed_client=attributed,
        )
        if reasoning_error:
            try:  # DFLASH_LOG_400_BODIES (gateway reject)
                from pathlib import Path as _P
                _log = _P(__file__).resolve().parents[1] / 'logs' / 'gateway-400-bodies.log'
                _log.parent.mkdir(parents=True, exist_ok=True)
                with _log.open('a', encoding='utf-8') as _fh:
                    _fh.write(
                        f"gateway_reject reason=reasoning_budget_too_low "
                        f"client={client_label!r} body={body[:2000]!r}\n"
                    )
            except Exception:
                pass
            raise HTTPException(
                status_code=400,
                detail={
                    'error': {
                        'message': reasoning_error,
                        'type': 'invalid_request_error',
                        'code': 400,
                        'reason': 'reasoning_budget_too_low',
                    }
                },
            )
        body = apply_reasoning_policy(
            body,
            reasoning=reasoning_model,
            disable_reasoning=disable_reasoning,
        )
    url = f"{_console_base(cfg)}/api/servers/{sid}/v1/chat/completions"
    filter_reasoning = disable_reasoning
    response = await _forward_chat(request, url, body, filter_reasoning=filter_reasoning)
    if isinstance(response, Response):
        response.headers['X-DFlash-Route'] = 'local'
        response.headers['X-DFlash-Server-Id'] = sid
    return response


@gateway_app.post('/v1/embeddings')
async def embeddings(request: Request) -> Response:
    _require_pipeline_active()
    cfg = load_config()
    server = _embed_server(cfg)
    sid = str(server.get('id') or '')
    url = f"{_console_base(cfg)}/api/servers/{sid}/v1/embeddings"
    return await _forward_chat(request, url)


@gateway_app.post('/v1/audio/speech')
async def audio_speech(request: Request) -> Response:
    _require_pipeline_active()
    cfg = load_config()
    url = f"{_console_base(cfg)}/api/runtimes/piper/v1/audio/speech"
    return await _forward_chat(request, url)


@gateway_app.post('/v1/audio/transcriptions')
async def audio_transcriptions(request: Request) -> Response:
    _require_pipeline_active()
    cfg = load_config()
    url = f"{_console_base(cfg)}/api/runtimes/stt/v1/audio/transcriptions"
    return await _forward_chat(request, url)
