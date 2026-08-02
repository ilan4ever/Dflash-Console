/** Shared API helpers for DFlash Console UI */
window.ConsoleApi = (function () {
  const inflightGets = new Map();
  const DEFAULT_GET_TIMEOUT_MS = 8000;

  async function api(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const dedupeKey = method === 'GET' ? path : '';
    const timeoutMs = Number.isFinite(options.timeoutMs)
      ? Math.max(500, Number(options.timeoutMs))
      : (method === 'GET' ? DEFAULT_GET_TIMEOUT_MS : 0);

    if (dedupeKey && inflightGets.has(dedupeKey)) {
      return inflightGets.get(dedupeKey);
    }

    const request = (async () => {
      const controller = timeoutMs > 0 ? new AbortController() : null;
      const timer = controller
        ? window.setTimeout(() => controller.abort(), timeoutMs)
        : null;
      const { timeoutMs: _ignoredTimeout, signal: callerSignal, ...fetchOptions } = options;
      try {
        const resp = await fetch(path, {
          cache: method === 'GET' ? 'no-store' : 'default',
          headers: { 'Content-Type': 'application/json', ...(fetchOptions.headers || {}) },
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
          throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return data;
      } catch (err) {
        if (err?.name === 'AbortError') {
          throw new Error(`Request timed out after ${timeoutMs}ms: ${path}`);
        }
        throw err;
      } finally {
        if (timer) window.clearTimeout(timer);
      }
    })();

    if (dedupeKey) {
      inflightGets.set(dedupeKey, request);
      request.finally(() => {
        if (inflightGets.get(dedupeKey) === request) inflightGets.delete(dedupeKey);
      });
    }

    return request;
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

  function toast(message, ok = true) {
    let el = document.getElementById('consoleToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'consoleToast';
      el.className = 'lm-toast hidden';
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = `lm-toast ${ok ? 'ok' : 'err'}`;
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(() => el.classList.add('hidden'), 3200);
  }

  return { api, toast, setSelectLoading };
})();
