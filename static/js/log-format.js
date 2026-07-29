/** Syntax-colored engine log lines, filters, and model-activity summaries. */
(function () {
  const LOG_FILTERS = [
    { id: 'all', label: 'All lines' },
    { id: 'errors', label: 'Errors only' },
    { id: 'warnings', label: 'Warnings' },
    { id: 'model', label: 'Model activity' },
    { id: 'engine', label: 'Engine lifecycle' },
    { id: 'inference', label: 'Inference' },
    { id: 'smart-model', label: 'Model summary' },
  ];

  const MODEL_HINTS = ['load', 'unload', 'ggml', 'tensor', 'offload', 'checkpoint', 'weights', 'kv cache', 'model', 'router'];
  const INFERENCE_HINTS = ['chat/completions', 'completion', 'prompt', 'token', 'decode', 'slot', 'inference', '/v1/', 'generat'];

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function span(cls, text) {
    return `<span class="${cls}">${escapeHtml(text)}</span>`;
  }

  function extractTimestamp(line) {
    const match = String(line || '').match(/\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/);
    return match ? match[0].replace('T', ' ') : '';
  }

  function isLlamaError(line) {
    return /^\d+\.\d+\.\d+\.\d+\s+E\s+/.test(String(line || ''));
  }

  function isLlamaWarn(line) {
    return /^\d+\.\d+\.\d+\.\d+\s+W\s+/.test(String(line || ''));
  }

  function isErrorLine(raw) {
    const line = String(raw || '');
    if (!line.trim()) return false;
    if (/^===.*\bfail/i.test(line)) return true;
    if (isLlamaError(line)) return true;
    if (/\[(ERROR|ERR)\]/i.test(line)) return true;
    if (/\b(exception|traceback|fatal)\b/i.test(line)) return true;
    if (/\berror\b/i.test(line) && !/\bno error\b/i.test(line)) return true;
    if (/\bfailed\b/i.test(line)) return true;
    return false;
  }

  function isWarnLine(raw) {
    const line = String(raw || '');
    if (!line.trim() || isErrorLine(line)) return false;
    if (/^===.*\bwarn/i.test(line)) return true;
    if (isLlamaWarn(line)) return true;
    if (/\[(WARN|WARNING)\]/i.test(line)) return true;
    if (/\bwarning\b/i.test(line)) return true;
    return false;
  }

  function isEngineLine(raw) {
    const line = String(raw || '');
    if (/^===\s*(boot|stop|router|model unload|legacy eject)/i.test(line)) return true;
    if (/\brouter idle ready\b/i.test(line)) return true;
    if (/\breleased GPU checkpoints\b/i.test(line)) return true;
    if (/\bengine restore\b/i.test(line)) return true;
    return false;
  }

  function isModelLine(raw) {
    const line = String(raw || '');
    if (!line.trim()) return false;
    if (isEngineLine(line)) return true;
    if (/cmd_child_to_router:state:/.test(line)) return true;
    const lower = line.toLowerCase();
    return MODEL_HINTS.some((hint) => lower.includes(hint));
  }

  function isInferenceLine(raw) {
    const lower = String(raw || '').toLowerCase();
    if (!lower.trim()) return false;
    return INFERENCE_HINTS.some((hint) => lower.includes(hint));
  }

  function matchesFilter(raw, filterId) {
    switch (filterId) {
      case 'errors':
        return isErrorLine(raw);
      case 'warnings':
        return isWarnLine(raw) || isErrorLine(raw);
      case 'model':
        return isModelLine(raw);
      case 'engine':
        return isEngineLine(raw);
      case 'inference':
        return isInferenceLine(raw);
      case 'all':
      default:
        return true;
    }
  }

  function filterLabel(filterId) {
    return LOG_FILTERS.find((entry) => entry.id === filterId)?.label || 'All lines';
  }

  function filterLogLines(lines, filterId) {
    const list = Array.isArray(lines) ? lines : [];
    if (!filterId || filterId === 'all') return list.slice();
    return list.filter((line) => matchesFilter(line, filterId));
  }

  function progressFromRouterPayload(payload) {
    if (!payload || typeof payload !== 'object') return null;
    const state = String(payload.state || '').toLowerCase();
    if (state !== 'loading') {
      return {
        kind: 'state',
        state: payload.state || 'unknown',
        model: payload.model || payload.payload?.model || '',
      };
    }
    const inner = payload.payload || {};
    const value = inner.value;
    if (typeof value !== 'number') return null;
    const stages = Array.isArray(inner.stages) ? inner.stages : [];
    const current = String(inner.current || '');
    if (stages.length) {
      const stageIndex = stages.indexOf(current);
      const idx = stageIndex >= 0 ? stageIndex : 0;
      const pct = ((idx + value) / stages.length) * 100;
      return {
        kind: 'progress',
        pct: Math.min(100, Math.max(0, pct)),
        stage: current || stages[idx] || 'loading',
      };
    }
    const pct = value <= 1 ? value * 100 : value;
    return {
      kind: 'progress',
      pct: Math.min(100, Math.max(0, pct)),
      stage: current || 'loading',
    };
  }

  function humanizeMarker(body) {
    const text = String(body || '').trim();
    if (/^boot\b/i.test(text)) {
      const tail = text.replace(/^boot\s+\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\s*/i, '');
      return `Engine boot · ${tail.replace(/\s+/g, ' ').trim()}`;
    }
    if (/^router idle ready/i.test(text)) return 'Router idle · ready to load';
    if (/^stop\b/i.test(text)) return 'Engine stopped';
    if (/^model unload/i.test(text)) {
      const model = text.match(/model=(\S+)/);
      return model ? `Model unloaded · ${model[1]}` : 'Model unloaded';
    }
    if (/^legacy eject/i.test(text)) return 'Legacy engine ejected · router idle';
    if (/fail/i.test(text)) return `Operation failed · ${text}`;
    return text;
  }

  function summaryLine(timestamp, message) {
    const ts = timestamp || '—';
    return `${ts} · ${message}`;
  }

  function summarizeModelActivity(lines) {
    const out = [];
    let lastProgressKey = '';
    let lastStateKey = '';

    for (const raw of Array.isArray(lines) ? lines : []) {
      const line = String(raw || '').trim();
      if (!line) continue;

      const marker = line.match(/^===\s*(.+?)\s*===$/);
      if (marker) {
        out.push(summaryLine(extractTimestamp(line), humanizeMarker(marker[1])));
        lastProgressKey = '';
        lastStateKey = '';
        continue;
      }

      const stateMatch = line.match(/cmd_child_to_router:state:(\{.*\})/);
      if (stateMatch) {
        try {
          const payload = JSON.parse(stateMatch[1]);
          const parsed = progressFromRouterPayload(payload);
          if (!parsed) continue;
          if (parsed.kind === 'progress') {
            const bucket = Math.floor(parsed.pct / 5);
            const key = `${parsed.stage}:${bucket}`;
            if (key === lastProgressKey) continue;
            lastProgressKey = key;
            out.push(summaryLine(
              extractTimestamp(line),
              `Loading · ${Math.round(parsed.pct)}% · ${parsed.stage}`,
            ));
            continue;
          }
          const stateKey = `${parsed.state}:${parsed.model || ''}`;
          if (stateKey === lastStateKey) continue;
          lastStateKey = stateKey;
          const modelSuffix = parsed.model ? ` · ${parsed.model}` : '';
          out.push(summaryLine(extractTimestamp(line), `Model state · ${parsed.state}${modelSuffix}`));
        } catch {
          /* ignore malformed router payloads */
        }
        continue;
      }

      if (isErrorLine(line) && isModelLine(line)) {
        out.push(summaryLine(extractTimestamp(line), `Error · ${line.replace(/^\d+\.\d+\.\d+\.\d+\s+[IWED]\s+\S+\s+/, '')}`));
        continue;
      }

      const lower = line.toLowerCase();
      if (/\b(http server listening|listening on|model loaded|load complete|ready)\b/i.test(line)
        && MODEL_HINTS.some((hint) => lower.includes(hint))) {
        const compact = line.replace(/^\d+\.\d+\.\d+\.\d+\s+[IWED]\s+\S+\s+/, '');
        out.push(summaryLine(extractTimestamp(line), compact));
      }
    }

    if (!out.length) return ['No model activity in this log window.'];
    return out;
  }

  function getDisplayLines(lines, filterId) {
    const list = Array.isArray(lines) ? lines : [];
    if (filterId === 'smart-model') return summarizeModelActivity(list);
    if (!filterId || filterId === 'all') return list.slice();
    return filterLogLines(list, filterId);
  }

  function highlightUrls(html) {
    return html.replace(
      /(https?:\/\/[^\s&lt;&quot;]+)/g,
      '<span class="log-url">$1</span>',
    );
  }

  function highlightDateTimes(html) {
    return html.replace(
      /\b(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\b/g,
      '<span class="log-datetime">$1</span>',
    );
  }

  function highlightBracketTags(html) {
    return html
      .replace(/\[(WARN|WARNING)\]/gi, '<span class="log-tag log-tag-warn">[$1]</span>')
      .replace(/\[(INFO)\]/gi, '<span class="log-tag log-tag-info">[$1]</span>')
      .replace(/\[(ERROR|ERR)\]/gi, '<span class="log-tag log-tag-error">[$1]</span>')
      .replace(/\[(DEBUG|DBG)\]/gi, '<span class="log-tag log-tag-dbg">[$1]</span>');
  }

  function highlightJson(jsonText) {
    let html = escapeHtml(jsonText);
    html = html.replace(/"([^"\\]+)"(\s*:)/g, '<span class="log-json-key">"$1"</span>$2');
    html = html.replace(/(:\s*)"([^"\\]*)"/g, '$1<span class="log-json-str">"$2"</span>');
    html = html.replace(/(:\s*)(-?\d+\.?\d*)([,}\]\s]|$)/g, '$1<span class="log-json-num">$2</span>$3');
    html = html.replace(/\b(true|false|null)\b/g, '<span class="log-json-lit">$1</span>');
    return html;
  }

  function highlightMessage(msg) {
    if (/^cmd_child_to_router:state:/.test(msg)) {
      const payload = msg.slice('cmd_child_to_router:state:'.length);
      return `<span class="log-event">cmd_child_to_router:state:</span>${highlightJson(payload)}`;
    }
    let html = escapeHtml(msg);
    html = highlightUrls(html);
    if (html.includes('{') && html.includes('}')) {
      html = highlightJson(html);
    }
    return html;
  }

  function highlightMarkerLine(raw) {
    let html = escapeHtml(raw);
    html = html.replace(
      /^(===\s*)(.+?)(\s*===)$/,
      '<span class="log-marker">$1</span><span class="log-marker-body">$2</span><span class="log-marker">$3</span>',
    );
    html = highlightDateTimes(html);
    html = html.replace(
      /\b(profile|router|idle|model)=([^\s=]+)/g,
      '<span class="log-kv-key">$1=</span><span class="log-kv-val">$2</span>',
    );
    return `<div class="log-line log-section">${html}</div>`;
  }

  function highlightSummaryLine(raw) {
    let html = escapeHtml(raw);
    html = html.replace(
      /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|—)\s·\s/,
      '<span class="log-datetime">$1</span><span class="log-dim"> · </span>',
    );
    html = html.replace(
      /\b(Engine boot|Router idle|Engine stopped|Model unloaded|Loading|Model state|Error|Operation failed)\b/g,
      '<span class="log-marker-body">$1</span>',
    );
    return `<div class="log-line log-summary">${html}</div>`;
  }

  function highlightLogLine(line) {
    const raw = String(line || '');
    if (!raw.trim()) return '<div class="log-line log-blank">&nbsp;</div>';

    if (/^===/.test(raw)) return highlightMarkerLine(raw);
    if (/^(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|—)\s·\s/.test(raw) || /^No model activity in this log window\./.test(raw)) {
      return highlightSummaryLine(raw);
    }

    const llama = raw.match(/^(\d+\.\d+\.\d+\.\d+)\s+([IWED])\s+(\S+)\s+(.*)$/);
    if (llama) {
      const [, ts, lvl, comp, msg] = llama;
      const lvlClass = {
        I: 'log-lvl-info',
        W: 'log-lvl-warn',
        E: 'log-lvl-error',
        D: 'log-lvl-dbg',
      }[lvl] || 'log-lvl-dbg';
      return `<div class="log-line">${span('log-ts', ts)} ${span(lvlClass, lvl)} ${span('log-comp', comp)} ${highlightMessage(msg)}</div>`;
    }

    let html = escapeHtml(raw);
    html = highlightBracketTags(html);
    html = highlightDateTimes(html);
    html = highlightUrls(html);
    if (raw.includes('{') && raw.includes('}')) {
      html = highlightJson(html);
    }

    let lineClass = 'log-line';
    if (/\[(WARN|WARNING)\]/i.test(raw)) lineClass += ' log-warn-line';
    else if (/\[(INFO)\]/i.test(raw)) lineClass += ' log-info-line';
    else if (/\[(ERROR|ERR)\]/i.test(raw)) lineClass += ' log-error-line';

    return `<div class="${lineClass}">${html}</div>`;
  }

  window.DFlashLogFormat = {
    LOG_FILTERS,
    highlightLogLine,
    filterLogLines,
    getDisplayLines,
    summarizeModelActivity,
    filterLabel,
    isErrorLine,
    isWarnLine,
    isModelLine,
    isEngineLine,
    isInferenceLine,
  };
})();
