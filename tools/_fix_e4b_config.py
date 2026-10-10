import json
from pathlib import Path

live = Path(r"C:\Users\ilanp\DFlash Console\config.json")
cfg = json.loads(live.read_text(encoding="utf-8"))
mmproj = r"C:\dev\Dflash-Console\models\google\gemma-4-E4B_q4_0-it\gemma-4-E4B-it-mmproj.gguf"
target = r"C:\dev\Dflash-Console\models\google\gemma-4-E4B_q4_0-it\gemma-4-E4B_q4_0-it.gguf"

kept = []
removed = []
for s in cfg.get("servers") or []:
    sid = str(s.get("id") or "")
    tpath = str(s.get("target_path") or "")
    # Drop accidental auto-register of the projector as a model
    if "mmproj" in sid.lower() or Path(tpath).name.lower().startswith("mmproj") or "mmproj" in Path(tpath).name.lower() and tpath.endswith(".gguf") and "E4B-it-mmproj" in tpath:
        removed.append(sid or tpath)
        continue
    if "e4b" in sid.lower():
        s["target_path"] = target
        s["mmproj_path"] = mmproj
        print("fixed", sid)
    kept.append(s)

cfg["servers"] = kept
live.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("removed", removed)
print("servers", len(kept))
