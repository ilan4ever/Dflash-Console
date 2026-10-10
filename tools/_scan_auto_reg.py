from pathlib import Path
p = Path(r"C:\dev\Dflash-Console\core\auto_register.py")
t = p.read_text(encoding="utf-8")
# show relevant helpers
for needle in ["mmproj", "def should", "def is_", "def discover", "def register"]:
    idx = 0
    while True:
        i = t.lower().find(needle.lower(), idx)
        if i < 0: break
        line = t[:i].count("\n") + 1
        print(f"L{line}: {t[i:i+120].splitlines()[0]}")
        idx = i + len(needle)
        if idx > 5000 and needle == "mmproj":
            break
print("--- snippet around mmproj mentions ---")
for i,line in enumerate(t.splitlines(),1):
    if "mmproj" in line.lower():
        print(f"{i}: {line}")
