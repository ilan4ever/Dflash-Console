from pathlib import Path

# --- chat_proxy.py: add resolver after validate_reasoning_chat_request ---
chat = Path(r"C:\dev\Dflash-Console\core\chat_proxy.py")
text = chat.read_text(encoding="utf-8")
needle = '''def validate_reasoning_chat_request(
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
'''
insert = needle + '''

def resolve_disable_reasoning_for_chat(
    raw: bytes,
    *,
    reasoning: bool,
    disable_header: bool,
    attributed_client: bool = False,
) -> tuple[bool, str | None]:
    """Decide whether to disable reasoning for one chat request.

    Returns ``(disable_reasoning, error_message)``. When ``error_message`` is
    set, the caller should reject with HTTP 400 ``reasoning_budget_too_low``.

    Integrators that send ``X-DFlash-Client`` (DeepSeek Harness and similar)
    often use low ``max_tokens`` for warmup / title calls. For those attributed
    clients, a too-small reasoning budget auto-heals to disable-reasoning
    instead of failing the turn — the same outcome as sending
    ``X-Disable-Reasoning: 1``.
    """
    disable = bool(disable_header)
    err = validate_reasoning_chat_request(
        raw,
        reasoning=reasoning,
        disable_reasoning=disable,
    )
    if err and attributed_client:
        return True, None
    return disable, err
'''
if "def resolve_disable_reasoning_for_chat(" in text:
    print("chat_proxy: already patched")
elif needle not in text:
    raise SystemExit("chat_proxy: needle not found")
else:
    chat.write_text(text.replace(needle, insert, 1), encoding="utf-8")
    print("chat_proxy: patched")

# --- gateway.py ---
gw = Path(r"C:\dev\Dflash-Console\api\gateway.py")
gtext = gw.read_text(encoding="utf-8")
old_gw = '''        from core.chat_proxy import apply_reasoning_policy, validate_reasoning_chat_request

        disable_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
        reasoning_model = model_has_reasoning(server)
        reasoning_error = validate_reasoning_chat_request(
            body,
            reasoning=reasoning_model,
            disable_reasoning=disable_reasoning,
        )
        if reasoning_error:
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
    filter_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
'''
new_gw = '''        from core.chat_proxy import apply_reasoning_policy, resolve_disable_reasoning_for_chat
        from core.client_identity import resolve_client_label, LABEL_UNKNOWN_API

        reasoning_model = model_has_reasoning(server)
        client_label = resolve_client_label(request)
        attributed = bool(client_label) and client_label != LABEL_UNKNOWN_API
        disable_reasoning, reasoning_error = resolve_disable_reasoning_for_chat(
            body,
            reasoning=reasoning_model,
            disable_header=request.headers.get('X-Disable-Reasoning') == '1',
            attributed_client=attributed,
        )
        if reasoning_error:
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
    filter_reasoning = disable_reasoning if body is not None else (
        request.headers.get('X-Disable-Reasoning') == '1'
    )
'''
# Careful: disable_reasoning may be undefined if body is None.
# Better initialize disable_reasoning before the if body is not None block.

old_gw2 = '''    if body is not None:
        from core.chat_proxy import apply_reasoning_policy, validate_reasoning_chat_request

        disable_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
        reasoning_model = model_has_reasoning(server)
        reasoning_error = validate_reasoning_chat_request(
            body,
            reasoning=reasoning_model,
            disable_reasoning=disable_reasoning,
        )
        if reasoning_error:
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
    filter_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
'''
new_gw2 = '''    disable_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
    if body is not None:
        from core.chat_proxy import apply_reasoning_policy, resolve_disable_reasoning_for_chat
        from core.client_identity import resolve_client_label, LABEL_UNKNOWN_API

        reasoning_model = model_has_reasoning(server)
        client_label = resolve_client_label(request)
        attributed = bool(client_label) and client_label != LABEL_UNKNOWN_API
        disable_reasoning, reasoning_error = resolve_disable_reasoning_for_chat(
            body,
            reasoning=reasoning_model,
            disable_header=disable_reasoning,
            attributed_client=attributed,
        )
        if reasoning_error:
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
'''
if "resolve_disable_reasoning_for_chat" in gtext and "attributed_client=attributed" in gtext:
    print("gateway: already patched")
elif old_gw2 not in gtext:
    raise SystemExit("gateway: needle not found")
else:
    gw.write_text(gtext.replace(old_gw2, new_gw2, 1), encoding="utf-8")
    print("gateway: patched")

# --- app.py ---
app = Path(r"C:\dev\Dflash-Console\api\app.py")
atext = app.read_text(encoding="utf-8")
old_app = '''    disable_reasoning = request.headers.get('X-Disable-Reasoning') == '1'
    reasoning_model = model_has_reasoning(server)
    reasoning_error = validate_reasoning_chat_request(
        raw,
        reasoning=reasoning_model,
        disable_reasoning=disable_reasoning,
    )
    if reasoning_error:
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
    raw = apply_reasoning_policy(
        raw,
        reasoning=reasoning_model,
        disable_reasoning=disable_reasoning,
    )
'''
new_app = '''    reasoning_model = model_has_reasoning(server)
    client_label = _request_client_label(request)
    from core.client_identity import LABEL_UNKNOWN_API
    from core.chat_proxy import resolve_disable_reasoning_for_chat

    attributed = bool(client_label) and client_label != LABEL_UNKNOWN_API
    disable_reasoning, reasoning_error = resolve_disable_reasoning_for_chat(
        raw,
        reasoning=reasoning_model,
        disable_header=request.headers.get('X-Disable-Reasoning') == '1',
        attributed_client=attributed,
    )
    if reasoning_error:
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
    raw = apply_reasoning_policy(
        raw,
        reasoning=reasoning_model,
        disable_reasoning=disable_reasoning,
    )
'''
# app.py already imports validate_reasoning_chat_request nearby - check if duplicate import of resolve is ok
if "resolve_disable_reasoning_for_chat(" in atext and "attributed_client=attributed" in atext:
    print("app: already patched")
elif old_app not in atext:
    raise SystemExit("app: needle not found")
else:
    # Also update the import of validate_reasoning_chat_request if it's a local import block
    atext2 = atext.replace(old_app, new_app, 1)
    app.write_text(atext2, encoding="utf-8")
    print("app: patched")

print("done")
