/** Shared API helpers for DFlash Console UI */
window.ConsoleApi = (function () {
  const DFLASH_UI_CLIENT = 'DFlash Console';
  const inflightGets = new Map();
  const DEFAULT_GET_TIMEOUT_MS = 8000;
  const SLOW_GET_TIMEOUT_MS = 60000;

  function defaultGetTimeoutMs(path) {
    const normalized = String(path || '').split('?')[0];
    if (normalized === '/api/diagnostics/bundle') {
      return SLOW_GET_TIMEOUT_MS;
    }
    if (normalized === '/api/servers' || normalized.startsWith('/api/servers/')) {
      return SLOW_GET_TIMEOUT_MS;
    }
    if (normalized === '/api/models' || normalized.startsWith('/api/models/')) {
      return 15000;
    }
    if (normalized === '/api/runtime-recommendations') {
      return 30000;
    }
    if (normalized.startsWith('/api/hf/')) {
      return SLOW_GET_TIMEOUT_MS;
    }
    return DEFAULT_GET_TIMEOUT_MS;
  }

  function formatApiError(detail) {
    if (detail == null) return 'Request failed';
    if (typeof detail === 'string') {
      const trimmed = detail.trim();
      if (!trimmed) return 'Request failed';
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed?.error?.message) return String(parsed.error.message);
      } catch (_) {
        /* not JSON */
      }
      return trimmed;
    }
    if (typeof detail === 'object' && detail.message) return String(detail.message);
    if (typeof detail === 'object' && detail.error?.message) return String(detail.error.message);
    try {
      return JSON.stringify(detail);
    } catch (_) {
      return String(detail);
    }
  }

  function requestHeaders(extra = {}) {
    return {
      'Content-Type': 'application/json',
      'X-DFlash-Client': DFLASH_UI_CLIENT,
      ...extra,
    };
  }

  function createInflightGet(dedupeKey, timeoutMs, options) {
    let controller = timeoutMs > 0 ? new AbortController() : null;
    let timer = null;

    const clearTimer = () => {
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const scheduleAbort = (ms) => {
      clearTimer();
      if (ms <= 0) return;
      timer = window.setTimeout(() => controller?.abort(), ms);
    };

    const extendTimeout = (nextTimeoutMs) => {
      if (nextTimeoutMs <= 0) {
        clearTimer();
        controller = null;
        return;
      }
      if (!controller) {
        controller = new AbortController();
      }
      scheduleAbort(nextTimeoutMs);
    };

    extendTimeout(timeoutMs);

    const promise = (async () => {
      const method = String(options.method || 'GET').toUpperCase();
      const { timeoutMs: _ignoredTimeout, signal: callerSignal, ...fetchOptions } = options;
      try {
        const resp = await fetch(dedupeKey, {
          cache: method === 'GET' ? 'no-store' : 'default',
          headers: requestHeaders(fetchOptions.headers || {}),
          ...fetchOptions,
          signal: callerSignal || controller?.signal,
        });
        let data = null;
        try {
          data = await resp.json();
        } catch (_) {
          data = null;
        }
        if (!resp.ok) {
          const detail = data?.detail || data?.error || `HTTP ${resp.status}`;
          const error = new Error(formatApiError(detail));
          error.apiDetail = detail;
          error.status = resp.status;
          throw error;
        }
        return data;
      } catch (err) {
        if (err?.name === 'AbortError') {
          const entry = inflightGets.get(dedupeKey);
          const activeMs = entry?.timeoutMs || timeoutMs;
          throw new Error(`Request timed out after ${activeMs}ms: ${dedupeKey}`);
        }
        throw explainNetworkError(err, dedupeKey);
      } finally {
        clearTimer();
      }
    })();

    const entry = { promise, extendTimeout, timeoutMs };
    inflightGets.set(dedupeKey, entry);
    // Clean up without creating an unhandled rejection: chaining .finally()
    // would leave a derived promise whose rejection nobody observes, so every
    // failed request also surfaced as an uncaught page error.
    const release = () => {
      if (inflightGets.get(dedupeKey) === entry) inflightGets.delete(dedupeKey);
    };
    promise.then(release, release);
    return entry;
  }

  function explainNetworkError(err, path) {
    if (err instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(String(err?.message || ''))) {
      const where = String(path || '').split('?')[0];
      const e = new Error(`Console API not reachable (${where || 'request'}). The Console server may be restarting or has stopped; wait a few seconds and retry, or restart DFlash Console.`);
      e.network = true;
      e.cause = err;
      return e;
    }
    return err;
  }

  async function api(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const dedupeKey = method === 'GET' ? path : '';
    let timeoutMs;
    if (options.timeoutMs != null && Number.isFinite(options.timeoutMs)) {
      timeoutMs = options.timeoutMs <= 0 ? 0 : Math.max(500, Number(options.timeoutMs));
    } else {
      timeoutMs = method === 'GET' ? defaultGetTimeoutMs(path) : 0;
    }

    if (dedupeKey && inflightGets.has(dedupeKey)) {
      const entry = inflightGets.get(dedupeKey);
      entry.timeoutMs = Math.max(entry.timeoutMs || 0, timeoutMs);
      entry.extendTimeout(entry.timeoutMs);
      return entry.promise;
    }

    if (dedupeKey) {
      return createInflightGet(dedupeKey, timeoutMs, options).promise;
    }

    const controller = timeoutMs > 0 ? new AbortController() : null;
    const timer = controller
      ? window.setTimeout(() => controller.abort(), timeoutMs)
      : null;
    const { timeoutMs: _ignoredTimeout, signal: callerSignal, ...fetchOptions } = options;
    try {
      const resp = await fetch(path, {
        cache: method === 'GET' ? 'no-store' : 'default',
        headers: requestHeaders(fetchOptions.headers || {}),
        ...fetchOptions,
        signal: callerSignal || controller?.signal,
      });
      let data = null;
      try {
        data = await resp.json();
      } catch (_) {
        data = null;
      }
      if (!resp.ok) {
        const detail = data?.detail || data?.error || `HTTP ${resp.status}`;
        const error = new Error(formatApiError(detail));
        error.apiDetail = detail;
        error.status = resp.status;
        throw error;
      }
      return data;
    } catch (err) {
      if (err?.name === 'AbortError') {
        throw new Error(`Request timed out after ${timeoutMs}ms: ${path}`);
      }
      throw explainNetworkError(err, path);
    } finally {
      if (timer) window.clearTimeout(timer);
    }
  }

  function setSelectLoading(selectEl, loading, message) {
    if (!selectEl) return;
    if (loading) {
      selectEl.disabled = true;
      selectEl.classList.add('is-loading');
      selectEl.innerHTML = `<option value="">${message || 'Loading…'}</option>`;
      window.DFlashSelectTheme?.syncSelect?.(selectEl);
      return;
    }
    selectEl.disabled = false;
    selectEl.classList.remove('is-loading');
    window.DFlashSelectTheme?.syncSelect?.(selectEl);
  }

  function toast(message, ok = true, options = {}) {
    let el = document.getElementById('consoleToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'consoleToast';
      el.className = 'lm-toast hidden';
      document.body.appendChild(el);
    }
    const showReport = !ok && options.report !== false;
    el.replaceChildren();
    if (showReport) {
      const msg = document.createElement('span');
      msg.className = 'lm-toast-message';
      msg.textContent = message;
      el.appendChild(msg);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lm-btn ghost small lm-toast-report-btn';
      btn.textContent = 'Report';
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        el.classList.add('hidden');
        el.classList.remove('has-report');
        window.DFlashBugReport?.quickReportFromError?.(
          message,
          options.reportContext || '',
        );
      });
      el.appendChild(btn);
      el.className = 'lm-toast err has-report';
    } else {
      el.textContent = message;
      el.className = `lm-toast ${ok ? 'ok' : 'err'}`;
    }
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(() => {
      el.classList.add('hidden');
      el.classList.remove('has-report');
    }, showReport ? 12000 : 3200);
  }

  return { api, toast, setSelectLoading, DFLASH_UI_CLIENT, requestHeaders };
})();
