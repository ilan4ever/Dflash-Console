/** Reload the page when the Console API restarts (new boot_id or reconnect after outage). */
(function () {
  const POLL_MS = 2000;

  let bootId = null;
  let offline = false;
  let reloading = false;

  async function check() {
    if (reloading) return;
    try {
      const resp = await fetch('/api/health', { cache: 'no-store' });
      if (!resp.ok) throw new Error('health unavailable');
      const data = await resp.json();
      const nextId = data?.boot_id ? String(data.boot_id) : '';
      const restarted = bootId && nextId && nextId !== bootId;
      if (restarted || (offline && nextId)) {
        reloading = true;
        window.DFlashStatusFeed?.note?.('Server restarted — refreshing page…');
        window.setTimeout(() => window.location.reload(), 500);
        return;
      }
      if (nextId) bootId = nextId;
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
