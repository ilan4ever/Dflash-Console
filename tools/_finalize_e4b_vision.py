import json, urllib.request, time, sys, os
from pathlib import Path

base = "http://127.0.0.1:8900"

def req(method, path, body=None, timeout=300):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(base+path, data=data, method=method, headers={"Content-Type":"application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            j = json.loads(raw)
        except Exception:
            j = {"raw": raw[:2000]}
        return e.code, j

code, rows = req("GET", "/api/servers")
servers = rows.get("servers") if isinstance(rows, dict) else rows
print("E4B/mmproj servers after restart:")
for s in servers or []:
    blob = json.dumps(s).lower()
    if "e4b" in blob or "mmproj" in blob:
        print({k: s.get(k) for k in ["id", "label", "port", "status", "mmproj_path", "target_path"]})

sid = "gemma-4-e4b-q4-0-it"
for attempt in range(12):
    code, j = req("POST", f"/api/servers/{sid}/load", {})
    print("load attempt", attempt, code, json.dumps(j)[:500])
    if code == 200:
        break
    if "boot already" in str(j):
        time.sleep(5)
        continue
    time.sleep(3)

code, rows = req("GET", "/api/servers")
servers = rows.get("servers") if isinstance(rows, dict) else rows
for s in servers or []:
    if s.get("id") == sid or (s.get("label") or "") == "gemma-4-E4B q4 0-it":
        print("final", {k: s.get(k) for k in ["id", "label", "port", "status", "mmproj_path"]})

os.environ["DFLASH_CONFIG"] = r"C:\Users\ilanp\DFlash Console\config.json"
sys.path[:0] = [r"C:\dev\Dflash-Console", r"C:\Users\ilanp\DFlash Console"]
from core.config import load_config, get_server, normalize_server
from core.chat_vision import ensure_vision_ready_for_chat, vision_capability
cfg = load_config()
s = normalize_server(get_server(cfg, sid) or {})
print("cap", vision_capability(s, cfg=cfg))
print("ensure", ensure_vision_ready_for_chat(s, cfg=cfg))
