/** Shared API helpers for DFlash Console UI */
window.ConsoleApi = (function () {
  async function api(path, options = {}) {
    const resp = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
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

  return { api, toast };
})();
