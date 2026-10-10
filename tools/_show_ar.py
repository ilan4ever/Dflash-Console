from pathlib import Path
p = Path(r"C:\dev\Dflash-Console\core\auto_register.py")
lines = p.read_text(encoding="utf-8").splitlines()
for i in range(560, 600):
    print(f"{i+1}: {lines[i]}")
