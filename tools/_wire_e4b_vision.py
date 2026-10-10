import json, urllib.request
from pathlib import Path
import sys
sys.path.insert(0, r"C:\dev\Dflash-Console")

# Force load from live config path if env known
import os
os.environ.setdefault("DFLASH_CONFIG", r"C:\Users\ilanp\DFlash Console\config.json")

from core.config import load_config, get_server, normalize_server, save_config
from core.vision_setup import resolve_mmproj_path, server_supports_vision_chat, wire_vision
from core.chat_vision import vision_capability, ensure_vision_ready_for_chat

cfg = load_config()
sid = "gemma-4-e4b-q4-0-it-2"
s = normalize_server(get_server(cfg, sid) or {})
print("server", s.get("id"), s.get("label"), s.get("profile"), s.get("mmproj_path"))
print("supports", server_supports_vision_chat(s, cfg=cfg))
print("resolve", resolve_mmproj_path(s, cfg=cfg))
print("cap", vision_capability(s, cfg=cfg))

# Try HTTP ensure / reload via console API
base = "http://127.0.0.1:8900"

def post(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base+path, data=data, method="POST", headers={"Content-Type":"application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            j = json.loads(body)
        except Exception:
            j = {"raw": body[:1000]}
        return e.code, j

# Discover vision endpoints
for p in [
    f"/api/servers/{sid}/vision/ensure",
    f"/api/servers/{sid}/ensure-vision",
    f"/api/vision/ensure",
    f"/api/servers/{sid}/reload",
    f"/api/servers/{sid}/load",
]:
    code, j = post(p, {} if "load" in p or "ensure" in p or "reload" in p else None)
    print(p, code, json.dumps(j)[:300])
