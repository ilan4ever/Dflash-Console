from pathlib import Path

def patch(path: Path):
    text = path.read_text(encoding="utf-8")
    old = "        if path.name.lower().startswith('mmproj'):\n            continue"
    new = "        # Google ships projectors as *-mmproj.gguf, not only mmproj-*.gguf\n        if 'mmproj' in path.name.lower():\n            continue"
    if old not in text:
        if "'mmproj' in path.name.lower()" in text:
            print("already patched", path)
            return
        raise SystemExit(f"pattern not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched", path)

src = Path(r"C:\dev\Dflash-Console\core\auto_register.py")
patch(src)
for dest in [
    Path(r"C:\Users\ilanp\DFlash Console\core\auto_register.py"),
    Path(r"C:\Users\ilanp\AppData\Local\Programs\DFlash Console\resources\console-runtime\core\auto_register.py"),
]:
    if dest.parent.is_dir():
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print("mirrored", dest)

# Remove bogus mmproj server from live API config via save if we can POST config, else disk+restart note
import json, urllib.request
live = Path(r"C:\Users\ilanp\DFlash Console\config.json")
cfg = json.loads(live.read_text(encoding="utf-8"))
before = [s.get("id") for s in cfg["servers"]]
cfg["servers"] = [
    s for s in cfg["servers"]
    if "mmproj" not in str(s.get("id") or "").lower()
    and "mmproj" not in Path(str(s.get("target_path") or "")).name.lower()
]
print("removed ids", set(before) - {s.get("id") for s in cfg["servers"]})
# Ensure E4B engines keep mmproj_path
mm = r"C:\dev\Dflash-Console\models\google\gemma-4-E4B_q4_0-it\gemma-4-E4B-it-mmproj.gguf"
tgt = r"C:\dev\Dflash-Console\models\google\gemma-4-E4B_q4_0-it\gemma-4-E4B_q4_0-it.gguf"
for s in cfg["servers"]:
    if "e4b" in str(s.get("id") or "").lower():
        s["target_path"] = tgt
        s["mmproj_path"] = mm
        print("ensured", s.get("id"))
live.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("disk servers", len(cfg["servers"]))
