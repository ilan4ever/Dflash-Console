# DFlash Console UI backup (v1)

Snapshot of the original DFlash Console interface before the left-sidebar redesign.

## Files

- `index.html` — main page
- `css/dflash-console.css` — full stylesheet
- `js/dflash-console-ui.js` — tab/modal/resize interactions
- `js/model-search-live.js` — Hugging Face search modal

## Roll back

1. Copy files back to `static/`:

```powershell
Copy-Item static\backup\console-ui-v1\index.html static\index.html -Force
Copy-Item static\backup\console-ui-v1\css\dflash-console.css static\css\dflash-console.css -Force
Copy-Item static\backup\console-ui-v1\js\dflash-console-ui.js static\js\dflash-console-ui.js -Force
Copy-Item static\backup\console-ui-v1\js\model-search-live.js static\js\model-search-live.js -Force
```

2. Restart DFlash Console.
