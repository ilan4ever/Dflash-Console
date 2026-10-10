import json
from pathlib import Path
cfg=json.loads(Path(r"C:\Users\ilanp\DFlash Console\config.json").read_text(encoding="utf-8"))
for s in cfg["servers"]:
    blob = json.dumps(s).lower()
    if "mmproj" in blob or "e4b" in str(s.get("id") or "").lower():
        print(s.get("id"), "->", Path(str(s.get("target_path") or "")).name)
print("count", len(cfg["servers"]))
t=Path(r"C:\dev\Dflash-Console\core\auto_register.py").read_text(encoding="utf-8")
print("patch present", "'mmproj' in path.name.lower()" in t)
