import json, urllib.request
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

code, cfgwrap = req("GET", "/api/config")
cfg = cfgwrap.get("config") or cfgwrap
servers = cfg.get("servers") or []
print("config servers with e4b/mmproj:")
for s in servers:
    sid = str(s.get("id") or "")
    if "e4b" in sid.lower() or "mmproj" in sid.lower():
        print(json.dumps({k: s.get(k) for k in ["id", "label", "port", "target_path", "mmproj_path", "enabled", "engine_on"]}, indent=2))

# Delete bogus mmproj engine if API supports it
for sid in ["gemma-4-e4b-it-mmproj"]:
    for path in [f"/api/servers/{sid}", f"/api/servers/{sid}/delete"]:
        c, j = req("DELETE", path)
        print("DELETE", path, c, str(j)[:200])

# Also strip from disk config and save via writing live file
live = Path(r"C:\Users\ilanp\DFlash Console\config.json")
on_disk = json.loads(live.read_text(encoding="utf-8"))
before = len(on_disk["servers"])
on_disk["servers"] = [s for s in on_disk["servers"] if "mmproj" not in str(s.get("id") or "").lower() and "mmproj" not in Path(str(s.get("target_path") or "")).name.lower()]
print("disk servers", before, "->", len(on_disk["servers"]))
live.write_text(json.dumps(on_disk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Prefer loading gemma-4-e4b-q4-0-it (API-known). If -2 missing from API config, Speak may resolve by label.
sid = "gemma-4-e4b-q4-0-it"
print("LOAD", sid)
code, j = req("POST", f"/api/servers/{sid}/load", {})
print("load", code, json.dumps(j)[:1000])

code, rows = req("GET", "/api/servers")
servers = rows.get("servers") if isinstance(rows, dict) else rows
for s in servers or []:
    if "e4b" in str(s.get("id") or "").lower() or "E4B" in str(s.get("label") or ""):
        print("after", {k: s.get(k) for k in ["id", "label", "port", "status", "mmproj_path"]})
