import json, urllib.request, time
from pathlib import Path
import sys, os
os.environ["DFLASH_CONFIG"] = r"C:\Users\ilanp\DFlash Console\config.json"
sys.path.insert(0, r"C:\dev\Dflash-Console")

# Prefer live process modules if importable from live root
live_root = Path(r"C:\Users\ilanp\DFlash Console")
if (live_root / "core" / "vision_setup.py").is_file():
    sys.path.insert(0, str(live_root))

from core.config import load_config, get_server, normalize_server
from core.vision_setup import resolve_mmproj_path, server_supports_vision_chat, VISION_CHAT_PROFILES
from core.chat_vision import vision_capability, ensure_vision_ready_for_chat

print("VISION_CHAT_PROFILES", sorted(VISION_CHAT_PROFILES))
print("gemma-ar in set", "gemma-ar" in VISION_CHAT_PROFILES)

cfg = load_config()
for sid in ["gemma-4-e4b-q4-0-it-2", "gemma-4-e4b-q4-0-it"]:
    s = normalize_server(get_server(cfg, sid) or {})
    print("---", sid)
    print("id", s.get("id"), "profile", s.get("profile"), "label", s.get("label"))
    print("target", s.get("target_path"))
    print("mmproj field", s.get("mmproj_path"))
    print("resolve", resolve_mmproj_path(s, cfg=cfg))
    print("supports", server_supports_vision_chat(s, cfg=cfg))
    print("cap", vision_capability(s, cfg=cfg))

base = "http://127.0.0.1:8900"

def req(method, path, body=None, timeout=180):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base+path, data=data, method=method, headers={"Content-Type":"application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            j = json.loads(raw)
        except Exception:
            j = {"raw": raw[:1500]}
        return e.code, j

# List servers for e4b
code, data = req("GET", "/api/servers")
rows = data.get("servers") if isinstance(data, dict) else data
for s in rows or []:
    if "e4b" in str(s.get("id","")).lower() or "E4B" in str(s.get("label","")) or "mmproj" in str(s.get("id","")).lower():
        print("API", {k:s.get(k) for k in ["id","label","port","status","mmproj_path","target_path","profile"]})

# Load the Speak-OneVoice engine
sid = "gemma-4-e4b-q4-0-it-2"
print("LOADING", sid)
code, j = req("POST", f"/api/servers/{sid}/load", {})
print("load", code, json.dumps(j)[:800])

# ensure vision via in-process against same config (API process should match after restart)
s = normalize_server(get_server(load_config(), sid) or {})
result = ensure_vision_ready_for_chat(s, cfg=load_config())
print("ensure_vision", json.dumps(result, default=str)[:1200])
