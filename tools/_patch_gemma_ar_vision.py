from pathlib import Path

path = Path(r"C:\dev\Dflash-Console\core\vision_setup.py")
text = path.read_text(encoding="utf-8")
old = """VISION_CHAT_PROFILES = frozenset({
    'gemma-chat',
    'gemma-12-dflash',
    'qwen-dflash',
    'qwen-ar',
    'gemma-12-ar',
})"""
new = """VISION_CHAT_PROFILES = frozenset({
    'gemma-chat',
    'gemma-ar',
    'gemma-12-dflash',
    'qwen-dflash',
    'qwen-ar',
    'gemma-12-ar',
})"""
if "'gemma-ar'" in text[text.find("VISION_CHAT_PROFILES"): text.find("VISION_CHAT_PROFILES") + 300]:
    print("gemma-ar already present")
elif old not in text:
    raise SystemExit("VISION_CHAT_PROFILES block not found as expected")
else:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("added gemma-ar to VISION_CHAT_PROFILES")

for dest in [
    Path(r"C:\Users\ilanp\DFlash Console\core\vision_setup.py"),
    Path(r"C:\Users\ilanp\AppData\Local\Programs\DFlash Console\resources\console-runtime\core\vision_setup.py"),
]:
    if dest.parent.is_dir():
        dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print("mirrored", dest)
    else:
        print("skip missing", dest)
