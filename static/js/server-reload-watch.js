/** Reload the page when the Console API restarts or static UI files change. */
(function () {
  const POLL_MS = 2000;

  let bootId = null;
  let uiVersion = null;
  let offline = false;
  let reloading = false;

  async function check() {
    if (reloading) return;
    try {
      const resp = await fetch('/api/health', { cache: 'no-store' });
      if (!resp.ok) throw new Error('health unavailable');
      const data = await resp.json();
      const nextId = data?.boot_id ? String(data.boot_id) : '';
      const nextUi = data?.ui_version ? String(data.ui_version) : '';
      const restarted = bootId && nextId && nextId !== bootId;
      const uiChanged = uiVersion && nextUi && nextUi !== uiVersion;
      if (restarted || (offline && nextId) || uiChanged) {
        reloading = true;
        const reason = uiChanged && !restarted ? 'UI updated — refreshing page…' : 'Server restarted — refreshing page…';
        window.DFlashStatusFeed?.note?.(reason);
        window.setTimeout(() => window.location.reload(), 500);
        return;
      }
      if (nextId) bootId = nextId;
      if (nextUi) uiVersion = nextUi;
      offline = false;
    } catch {
      offline = true;
    }
  }

  function start() {
    void check();
    window.setInterval(check, POLL_MS);
  }

  document.addEventListener('DOMContentLoaded', start);
})();
