from pathlib import Path
app = Path(r"C:\dev\Dflash-Console\api\app.py")
text = app.read_text(encoding="utf-8")
old_imp = """    from core.chat_proxy import (
        apply_reasoning_policy,
        chat_upstream_read_timeout,
        empty_completion_guard,
        estimate_request_context,
        extract_stream_completion_stats,
        is_reasoning_only_chunk,
        open_upstream_chat_stream,
        sse_had_content_delta,
        sse_stream_complete,
        sse_stream_error_chunk,
        SSE_KEEPALIVE_COMMENT,
        upstream_chat_completion,
        validate_reasoning_chat_request,
        wants_stream,
    )
"""
new_imp = """    from core.chat_proxy import (
        apply_reasoning_policy,
        chat_upstream_read_timeout,
        empty_completion_guard,
        estimate_request_context,
        extract_stream_completion_stats,
        is_reasoning_only_chunk,
        open_upstream_chat_stream,
        resolve_disable_reasoning_for_chat,
        sse_had_content_delta,
        sse_stream_complete,
        sse_stream_error_chunk,
        SSE_KEEPALIVE_COMMENT,
        upstream_chat_completion,
        wants_stream,
    )
    from core.client_identity import LABEL_UNKNOWN_API
"""
old_body = """    reasoning_model = model_has_reasoning(server)
    client_label = _request_client_label(request)
    from core.client_identity import LABEL_UNKNOWN_API
    from core.chat_proxy import resolve_disable_reasoning_for_chat

    attributed = bool(client_label) and client_label != LABEL_UNKNOWN_API
"""
new_body = """    reasoning_model = model_has_reasoning(server)
    attributed = bool(client_label) and client_label != LABEL_UNKNOWN_API
"""
if old_imp not in text:
    raise SystemExit("import block not found")
if old_body not in text:
    raise SystemExit("body block not found")
text = text.replace(old_imp, new_imp, 1).replace(old_body, new_body, 1)
app.write_text(text, encoding="utf-8")
print("app imports cleaned")
