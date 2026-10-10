from pathlib import Path
path = Path(r"static/css/dflash-shell.css")
text = path.read_text(encoding="utf-8")
old = """  html.df-narrow .df-shell .lm-view[data-view=\"server\"] .lm-server-cards .lm-mobile-engine-head-chip .lm-tag.dflash-logo-label,
  html.df-narrow .df-shell .lm-view[data-view=\"server\"] .lm-server-cards .lm-mobile-engine-head-chip .dflash-logo-label {
    display: inline-flex !important;
    width: 48px;
    min-width: 48px;
    height: 14px;
    margin: 0;
  }"""
new = """  html.df-narrow .df-shell .lm-view[data-view=\"server\"] .lm-server-cards .lm-mobile-engine-head-chip .lm-tag.dflash-logo-label,
  html.df-narrow .df-shell .lm-view[data-view=\"server\"] .lm-server-cards .lm-mobile-engine-head-chip .dflash-logo-label {
    display: inline-flex !important;
    width: 56px;
    min-width: 56px;
    height: 15px;
    margin: 0;
    padding: 0 4px 0 6px;
    box-sizing: border-box;
    background-size: contain;
    background-position: left center;
    background-origin: content-box;
    background-clip: content-box;
  }"""
if old not in text:
    raise SystemExit("mobile block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("ok")
