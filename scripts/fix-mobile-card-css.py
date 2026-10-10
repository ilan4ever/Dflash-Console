import re
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "static/css/dflash-shell.css"
lines = path.read_text(encoding="utf-8").splitlines()

def is_selector_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s.startswith("/*") or s.startswith("*"):
        return False
    return (
        "lm-view[data-view=" in line
        or ".df-shell .lm-view[data-view=" in line
        or (line.startswith("  html.df-narrow") and ".lm-model-card" in line)
        or (line.startswith("  .df-shell") and ".lm-model-card" in line)
    )

def is_property_line(line: str) -> bool:
    s = line.strip()
    if not s or s in ("{", "}"):
        return False
    if is_selector_line(line):
        return False
    return line.startswith("    ") or s.endswith(";") or s.endswith(":")

out: list[str] = []
i = 0
while i < len(lines):
    if is_selector_line(lines[i]):
        group: list[str] = []
        while i < len(lines) and is_selector_line(lines[i]):
            raw = lines[i].rstrip()
            if raw.endswith("{"):
                raw = raw[:-1].rstrip()
            group.append(raw)
            i += 1
        if not group:
            continue
        for j, sel in enumerate(group):
            suffix = "," if j < len(group) - 1 else " {"
            out.append(f"{sel}{suffix}")
        continue
    out.append(lines[i])
    i += 1

text = "\n".join(out) + "\n"
path.write_text(text, encoding="utf-8", newline="\n")
print("fixed selector groups")
