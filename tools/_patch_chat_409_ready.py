from pathlib import Path

path = Path("api/app.py")
text = path.read_text(encoding="utf-8")

old_ready_start = """    live = build_server_status(server, cfg=cfg)
    if live.get('loaded_models'):
        if live.get('status') != 'loaded':
            live = {**live, 'status': 'loaded'}
        # Already loaded.  Auto-grow if this request needs more context than
        # the loaded model provides; otherwise share the model as-is.
        if required_context and cfg.get('context_auto_grow') is not False:
            loaded_ctx = _loaded_per_slot_context(server)
            if loaded_ctx and required_context > loaded_ctx:
                return _grow_context_for_chat(server_id, server, cfg, required_context, client_label=client_label)
        note_engine_active_client(server_id, client_label=client_label)
        return live

    if live.get('status') == 'booting':
        return _wait_until_loaded()
"""

new_ready_start = """    from core.inference_stats import is_proxy_generating

    def _status_as_loaded(status: dict[str, Any]) -> dict[str, Any]:
        if status.get('status') != 'loaded':
            status = {**status, 'status': 'loaded'}
        return status

    def _synthetic_loaded_from_config() -> dict[str, Any]:
        \"\"\"When a probe fails under concurrent load, trust the configured id.\"\"\"
        model_id = str(server.get('model_id') or '').strip()
        loaded = [model_id] if model_id else []
        return {
            **server,
            'running': True,
            'status': 'loaded' if loaded else 'running',
            'booting': False,
            'loaded_models': loaded,
            'active_model_id': loaded[0] if loaded else '',
            'ready_for_chat': bool(loaded),
        }

    live = build_server_status(server, cfg=cfg)

    # Another chat is already mid-flight on this engine: never JIT-load (that
    # path raises model_already_loaded_elsewhere / stack-repair 409s and can
    # stop_server under DFlash draft profiles). Share the live or configured id.
    if is_proxy_generating(server_id):
        if not live.get('loaded_models'):
            live = _synthetic_loaded_from_config()
        live = _status_as_loaded(live)
        if required_context and cfg.get('context_auto_grow') is not False:
            loaded_ctx = _loaded_per_slot_context(server)
            if loaded_ctx and required_context > loaded_ctx:
                # Growing reloads the engine — wait until the active turn ends.
                deadline = time.time() + 180.0
                while time.time() < deadline and is_proxy_generating(server_id):
                    time.sleep(0.05)
                return _ensure_server_ready_for_chat(
                    server_id,
                    server,
                    cfg,
                    client_label=client_label,
                    required_context=required_context,
                )
        note_engine_active_client(server_id, client_label=client_label)
        return live

    if live.get('loaded_models'):
        live = _status_as_loaded(live)
        # Already loaded.  Auto-grow if this request needs more context than
        # the loaded model provides; otherwise share the model as-is.
        if required_context and cfg.get('context_auto_grow') is not False:
            loaded_ctx = _loaded_per_slot_context(server)
            if loaded_ctx and required_context > loaded_ctx:
                return _grow_context_for_chat(server_id, server, cfg, required_context, client_label=client_label)
        note_engine_active_client(server_id, client_label=client_label)
        return live

    # Probe can return empty loaded_models while llama is busy serving another
    # client. Retry briefly before treating the engine as idle for JIT load.
    host = str(server.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    port = int(server.get('port') or 0)
    if port > 0 and tcp_port_open(host, port):
        for _ in range(6):
            time.sleep(0.05)
            live = build_server_status(server, cfg=cfg)
            if live.get('loaded_models') or is_proxy_generating(server_id):
                break
        if is_proxy_generating(server_id) and not live.get('loaded_models'):
            live = _synthetic_loaded_from_config()
        if live.get('loaded_models'):
            live = _status_as_loaded(live)
            note_engine_active_client(server_id, client_label=client_label)
            return live

    if live.get('status') == 'booting':
        return _wait_until_loaded()
"""

if old_ready_start not in text:
    raise SystemExit("OLD ready block not found")
text = text.replace(old_ready_start, new_ready_start, 1)
path.write_text(text, encoding="utf-8")
print("patched _ensure_server_ready_for_chat")
