from pathlib import Path

path = Path("api/app.py")
text = path.read_text(encoding="utf-8")

# 1) Acquire gate just before ensure_ready in local path
old_ensure = """        required_context = max(estimated_context or 0, header_context or 0, body_context or 0)
        live = _ensure_server_ready_for_chat(
            server_id,
            server,
            cfg,
            client_label=_request_client_label(request),
            required_context=required_context or None,
        )
"""

new_ensure = """        required_context = max(estimated_context or 0, header_context or 0, body_context or 0)
        from core.chat_queue import chat_server_gate

        _chat_gate = chat_server_gate(server_id)
        await _chat_gate.acquire()
        _chat_gate_held = True
        try:
            live = _ensure_server_ready_for_chat(
                server_id,
                server,
                cfg,
                client_label=_request_client_label(request),
                required_context=required_context or None,
            )
        except Exception:
            if _chat_gate_held:
                _chat_gate.release()
                _chat_gate_held = False
            raise
"""

if old_ensure not in text:
    raise SystemExit("ensure block not found")
text = text.replace(old_ensure, new_ensure, 1)

# 2) Init flags for adapter path so later release is safe
old_adapter_live = """        live = {
            'status': 'loaded',
            'active_model_id': served_id,
            'loaded_models': [served_id] if served_id else [],
        }
        raw = await request.body()
"""
new_adapter_live = """        live = {
            'status': 'loaded',
            'active_model_id': served_id,
            'loaded_models': [served_id] if served_id else [],
        }
        _chat_gate = None
        _chat_gate_held = False
        raw = await request.body()
"""
if old_adapter_live not in text:
    raise SystemExit("adapter live block not found")
text = text.replace(old_adapter_live, new_adapter_live, 1)

# 3) After stream mark_inference_start + open, release gate before StreamingResponse
# Find the stream open success path - release after open_upstream, and on exceptions too

old_stream = """    if wants_stream(raw):
        mark_inference_start(
            server_id,
            api_url=api_url,
            model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
            client_label=client_label,
        )
        close_upstream = None
        read_timeout = chat_upstream_read_timeout(cfg)
        try:
            media_type, chunks, close_upstream = await open_upstream_chat_stream(
                url,
                raw,
                content_type=content_type,
                server_id=server_id,
                read_timeout=read_timeout,
            )
        except urllib.error.HTTPError as exc:
            mark_inference_end(server_id, client_label=client_label)
            detail = exc.read().decode('utf-8', errors='replace')
            raise HTTPException(status_code=exc.code, detail=detail) from exc
        except Exception as exc:
            mark_inference_end(server_id, client_label=client_label)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
"""

new_stream = """    if wants_stream(raw):
        mark_inference_start(
            server_id,
            api_url=api_url,
            model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
            client_label=client_label,
        )
        close_upstream = None
        read_timeout = chat_upstream_read_timeout(cfg)
        try:
            media_type, chunks, close_upstream = await open_upstream_chat_stream(
                url,
                raw,
                content_type=content_type,
                server_id=server_id,
                read_timeout=read_timeout,
            )
        except urllib.error.HTTPError as exc:
            mark_inference_end(server_id, client_label=client_label)
            if _chat_gate_held and _chat_gate is not None:
                _chat_gate.release()
                _chat_gate_held = False
            detail = exc.read().decode('utf-8', errors='replace')
            raise HTTPException(status_code=exc.code, detail=detail) from exc
        except Exception as exc:
            mark_inference_end(server_id, client_label=client_label)
            if _chat_gate_held and _chat_gate is not None:
                _chat_gate.release()
                _chat_gate_held = False
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if _chat_gate_held and _chat_gate is not None:
            _chat_gate.release()
            _chat_gate_held = False
"""

if old_stream not in text:
    raise SystemExit("stream block not found")
text = text.replace(old_stream, new_stream, 1)

# 4) Non-stream: release after mark_inference_start, before to_thread
old_block = """    mark_inference_start(
        server_id,
        api_url=api_url,
        model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
        client_label=client_label,
    )
    active_model = str(live.get('active_model_id') or server.get('model_id') or '')
    disconnect_task = asyncio.create_task(
"""

new_block = """    mark_inference_start(
        server_id,
        api_url=api_url,
        model_id=str(live.get('active_model_id') or server.get('model_id') or ''),
        client_label=client_label,
    )
    if _chat_gate_held and _chat_gate is not None:
        _chat_gate.release()
        _chat_gate_held = False
    active_model = str(live.get('active_model_id') or server.get('model_id') or '')
    disconnect_task = asyncio.create_task(
"""

if old_block not in text:
    raise SystemExit("non-stream mark block not found")
text = text.replace(old_block, new_block, 1)

# 5) Release gate on early HTTPException between ensure and stream (model_mismatch / reasoning)
# Wrap the shared section after the if/else in a try that releases on HTTPException.
# Safer: before each raise HTTPException in that section - too many.
# Instead init _chat_gate_held = False at function start for all paths.

old_fn = """async def proxy_chat_completions(server_id: str, request: Request):
    import asyncio
    import json
    import urllib.error
"""
new_fn = """async def proxy_chat_completions(server_id: str, request: Request):
    import asyncio
    import json
    import urllib.error

    _chat_gate = None
    _chat_gate_held = False
"""
if old_fn not in text:
    raise SystemExit("fn start not found")
text = text.replace(old_fn, new_fn, 1)

# Remove duplicate init in adapter path
text = text.replace(
    """        _chat_gate = None
        _chat_gate_held = False
        raw = await request.body()
""",
    """        raw = await request.body()
""",
    1,
)

# 6) On reasoning_error and model_mismatch raises, release gate
old_reason = """    if reasoning_error:
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
"""
new_reason = """    if reasoning_error:
        if _chat_gate_held and _chat_gate is not None:
            _chat_gate.release()
            _chat_gate_held = False
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
"""
if old_reason not in text:
    raise SystemExit("reasoning_error block not found")
text = text.replace(old_reason, new_reason, 1)

old_mm = """            if (
                request_strict_model_match(request)
                and requested_model
                and not model_ids_compatible(requested_model, upstream_model_id)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        'error': {
                            'message': (
                                f"Model mismatch: requested '{requested_model}' but engine "
                                f"has '{upstream_model_id}' loaded."
                            ),
                            'type': 'invalid_request_error',
                            'code': 'model_mismatch',
                            'param': 'model',
                            'requested_model': requested_model,
                            'active_model_id': upstream_model_id,
                        }
                    },
                )
"""
new_mm = """            if (
                request_strict_model_match(request)
                and requested_model
                and not model_ids_compatible(requested_model, upstream_model_id)
            ):
                if _chat_gate_held and _chat_gate is not None:
                    _chat_gate.release()
                    _chat_gate_held = False
                raise HTTPException(
                    status_code=409,
                    detail={
                        'error': {
                            'message': (
                                f"Model mismatch: requested '{requested_model}' but engine "
                                f"has '{upstream_model_id}' loaded."
                            ),
                            'type': 'invalid_request_error',
                            'code': 'model_mismatch',
                            'param': 'model',
                            'requested_model': requested_model,
                            'active_model_id': upstream_model_id,
                        }
                    },
                )
"""
if old_mm not in text:
    raise SystemExit("model_mismatch block not found")
text = text.replace(old_mm, new_mm, 1)

# 7) api_url missing
old_base = """    if not base:
        raise HTTPException(status_code=400, detail='engine api_url not configured')
"""
new_base = """    if not base:
        if _chat_gate_held and _chat_gate is not None:
            _chat_gate.release()
            _chat_gate_held = False
        raise HTTPException(status_code=400, detail='engine api_url not configured')
"""
# Only replace the one in proxy_chat_completions - first occurrence after our edits near chat
idx = text.find("async def proxy_chat_completions")
if idx < 0:
    raise SystemExit("proxy fn missing")
sub = text[idx:]
if old_base not in sub:
    raise SystemExit("base check not found in proxy")
sub2 = sub.replace(old_base, new_base, 1)
text = text[:idx] + sub2

path.write_text(text, encoding="utf-8")
print("patched proxy_chat_completions gate")
