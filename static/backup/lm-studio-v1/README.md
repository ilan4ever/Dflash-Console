# LM Studio-style UI backup (v1)

Snapshot of the original DFlash Studio interface before the left-sidebar redesign.

## Files

- `index.html` — main page
- `css/lm-studio-mockup.css` — full stylesheet
- `js/lm-studio-ui.js` — tab/modal/resize interactions
- `js/model-search-live.js` — Hugging Face search modal

## Roll back

1. Copy files back to `static/`:

```powershell
cd C:\dev\Dflash-Studio
Copy-Item static\backup\lm-studio-v1\index.html static\index.html -Force
Copy-Item static\backup\lm-studio-v1\css\lm-studio-mockup.css static\css\lm-studio-mockup.css -Force
Copy-Item static\backup\lm-studio-v1\js\lm-studio-ui.js static\js\lm-studio-ui.js -Force
Copy-Item static\backup\lm-studio-v1\js\model-search-live.js static\js\model-search-live.js -Force
Remove-Item static\css\dflash-shell.css -ErrorAction SilentlyContinue
```

2. Hard-refresh the browser (Ctrl+Shift+R).

Created: 2026-07-29
