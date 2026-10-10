from pathlib import Path
path = Path("api/app.py")
text = path.read_text(encoding="utf-8")
old = """            if loaded_ctx and required_context > loaded_ctx:
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
"""
new = """            if loaded_ctx and required_context > loaded_ctx:
                # Growing reloads the engine — wait until the active turn ends.
                deadline = time.time() + 180.0
                while time.time() < deadline and is_proxy_generating(server_id):
                    time.sleep(0.05)
                if not is_proxy_generating(server_id):
                    return _ensure_server_ready_for_chat(
                        server_id,
                        server,
                        cfg,
                        client_label=client_label,
                        required_context=required_context,
                    )
                # Still busy after wait: serve with current context rather than 409.
"""
if old not in text:
    raise SystemExit("grow-while-generating block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("fixed grow wait recurse")
