# patch_engine_token_metrics_overlap.py
# Fixes desktop Engines card LAST/LIVE token metrics overlap.
from pathlib import Path

ROOT = Path(r"C:\dev\Dflash-Console")
JS = ROOT / "static" / "js" / "server-live.js"
CSS = ROOT / "static" / "css" / "dflash-shell.css"

js = JS.read_text(encoding="utf-8")
css = CSS.read_text(encoding="utf-8")

OLD_ENSURE = """  function ensureLiveTokenMetricsDom(host) {
    if (!host) return;
    const hasLive = host.querySelector('.lm-token-metrics-live');
    const hasLast = host.querySelector('.lm-model-card-token-last');
    if (hasLive && !hasLast) return;
    host.innerHTML = liveTokenMetricsRowHtml();
  }"""

NEW_ENSURE = """  function ensureLiveTokenMetricsDom(host) {
    if (!host) return;
    const hasLive = !!host.querySelector('.lm-token-metrics-live');
    const hasLast = !!host.querySelector('.lm-model-card-token-last');
    // Pure live shell only: keep. Any last, missing live, or both -> replace with live-only HTML.
    if (hasLive && !hasLast) return;
    host.innerHTML = liveTokenMetricsRowHtml();
  }"""

OLD_PATCH = """  function patchLiveTokenMetricsHost(host, server, row) {
    const stats = row?.inference_stats || server?.inference_stats || {};
    let slot = liveMetricsSlotForEntry(server, row);
    if (!slot?.generating && inferenceIsGenerating(stats)) {
      slot = { slot_id: 0, ...stats, generating: true, ...(slot || {}) };
    }
    if (!slot?.generating) return false;
    ensureLiveTokenMetricsDom(host);"""

NEW_PATCH = """  function patchLiveTokenMetricsHost(host, server, row, opts = {}) {
    const forceLiveShell = !!(opts && opts.forceLiveShell);
    const stats = row?.inference_stats || server?.inference_stats || {};
    let slot = liveMetricsSlotForEntry(server, row);
    if (!slot?.generating && inferenceIsGenerating(stats)) {
      slot = { slot_id: 0, ...stats, generating: true, ...(slot || {}) };
    }
    // When the card is known generating, install/clear to a live shell even if slot detection fails,
    // so LAST metrics never remain visible under .generating.
    if (!slot?.generating) {
      if (forceLiveShell) {
        ensureLiveTokenMetricsDom(host);
      }
      return false;
    }
    ensureLiveTokenMetricsDom(host);"""

OLD_SYNC = """      let hasMetrics = false;
      if (generating) {
        if (isMobileEngineCards()) {
          host.querySelectorAll('.lm-model-card-token-last').forEach((el) => el.remove());
        }
        hasMetrics = patchLiveTokenMetricsHost(host, server, row);
        if (!hasMetrics && isMobileEngineCards() && host.querySelector('.lm-token-metrics-live')) {
          hasMetrics = true;
        }
      } else {"""

NEW_SYNC = """      let hasMetrics = false;
      if (generating) {
        // Desktop + mobile: never leave LAST metrics visible while generating (overlap bug on desktop).
        host.querySelectorAll('.lm-model-card-token-last').forEach((el) => el.remove());
        if (!host.querySelector('.lm-token-metrics-live')) {
          host.innerHTML = liveTokenMetricsRowHtml();
        }
        hasMetrics = patchLiveTokenMetricsHost(host, server, row, { forceLiveShell: true });
        if (!hasMetrics && host.querySelector('.lm-token-metrics-live')) {
          hasMetrics = true;
        }
      } else {"""

changes = 0
if OLD_ENSURE not in js:
    raise SystemExit("OLD_ENSURE not found")
js = js.replace(OLD_ENSURE, NEW_ENSURE, 1)
changes += 1

if OLD_PATCH not in js:
    raise SystemExit("OLD_PATCH not found")
js = js.replace(OLD_PATCH, NEW_PATCH, 1)
changes += 1

if OLD_SYNC not in js:
    raise SystemExit("OLD_SYNC not found")
js = js.replace(OLD_SYNC, NEW_SYNC, 1)
changes += 1

CSS_MARKER = """html:not(.df-narrow) .df-shell .lm-desktop-engine-foot-metrics .lm-token-metrics-live .lm-model-card-token-generating {
  justify-content: flex-end;
  width: auto;
  flex-wrap: nowrap;
}"""

CSS_INSERT = """html:not(.df-narrow) .df-shell .lm-desktop-engine-foot-metrics .lm-token-metrics-live .lm-model-card-token-generating {
  justify-content: flex-end;
  width: auto;
  flex-wrap: nowrap;
}

/* Desktop: hide LAST completion metrics while card is generating (live-only foot). */
.df-shell .lm-model-card-compact.generating [data-engine-live-metrics] .lm-model-card-token-last,
.df-shell .lm-model-card-compact.generating .lm-model-card-token-metric.lm-model-card-token-last {
  display: none !important;
}"""

if CSS_MARKER not in css:
    raise SystemExit("CSS_MARKER not found")
if "lm-model-card-compact.generating [data-engine-live-metrics] .lm-model-card-token-last" in css:
    print("CSS rule already present; skipping CSS insert")
else:
    css = css.replace(CSS_MARKER, CSS_INSERT, 1)
    changes += 1

JS.write_text(js, encoding="utf-8")
CSS.write_text(css, encoding="utf-8")
print(f"OK: applied {changes} replacements")
print(f"Wrote {JS}")
print(f"Wrote {CSS}")
