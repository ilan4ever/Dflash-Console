from pathlib import Path
root = Path(r"C:\dev\Dflash-Console")
js = (root / "static/js/server-live.js").read_text(encoding="utf-8")
css = (root / "static/css/dflash-shell.css").read_text(encoding="utf-8")
settings = (root / "static/js/settings-live.js").read_text(encoding="utf-8") if (root / "static/js/settings-live.js").exists() else ""
gen = js.split("if (generating) {", 1)[1].split("} else {", 1)[0]
checks = [
    ("unconditional last remove in generating path", ".lm-model-card-token-last').forEach" in gen and "isMobileEngineCards()" not in gen.split("forEach")[0]),
    ("no mobile-only gate around last remove", "if (isMobileEngineCards())" not in gen),
    ("forceLiveShell true passed", "forceLiveShell: true" in gen),
    ("install live shell when missing", "liveTokenMetricsRowHtml()" in gen),
    ("placeholder hasMetrics when live present", "host.querySelector('.lm-token-metrics-live')" in gen),
    ("ensure hardened", "Pure live shell only" in js),
    ("patch forceLiveShell param", "opts = {}" in js and "forceLiveShell" in js),
    ("CSS desktop hide last", "lm-model-card-compact.generating [data-engine-live-metrics] .lm-model-card-token-last" in css),
    ("CSS mobile narrow rule kept", "lm-mobile-engine-tokens .lm-model-card-token-last" in css and "html.df-narrow" in css),
    ("settings-live.js unchanged by us (no forceLiveShell)", "forceLiveShell" not in settings),
]
failed = 0
for name, ok in checks:
    print(("PASS" if ok else "FAIL"), name)
    failed += 0 if ok else 1
print("generating-block isMobileEngineCards count:", gen.count("isMobileEngineCards()"))
print("RESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
