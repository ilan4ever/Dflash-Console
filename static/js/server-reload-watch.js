/** Reload the page when the Console API restarts or static UI files change. */
(function () {
  const POLL_MS = 5000;
  const HEALTH_TIMEOUT_MS = 2500;

  let bootId = null;
  let uiVersion = null;
  let offline = false;
  let reloading = false;
  let checking = false;
  let timer = null;
  let badgeEl = null;
  const bootStorageKey = 'dflashConsole.lastSeenBootId';

  function badge() {
    if (!badgeEl) badgeEl = document.getElementById('serverLinkBadge');
    return badgeEl;
  }

  function setConnectionBadge(isOnline) {
    const el = badge();
    if (!el) return;
    el.textContent = isOnline ? 'Online' : 'Offline';
    el.classList.toggle('online', isOnline);
    el.classList.toggle('offline', !isOnline);
    el.title = isOnline ? 'Console API connected' : 'Console API unreachable — start or restart the server';
  }

  async function check() {
    if (reloading || checking) return;
    checking = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
    try {
      const resp = await fetch('/api/health', { cache: 'no-store', signal: controller.signal });
      if (!resp.ok) throw new Error('health unavailable');
      const data = await resp.json();
      const nextId = data?.boot_id ? String(data.boot_id) : '';
      const nextUi = data?.ui_version ? String(data.ui_version) : '';
      const storedBootId = sessionStorage.getItem(bootStorageKey) || '';
      const restarted = (bootId && nextId && nextId !== bootId)
        || (storedBootId && nextId && nextId !== storedBootId);
      const uiChanged = uiVersion && nextUi && nextUi !== uiVersion;
      setConnectionBadge(true);
      if (restarted || (offline && nextId) || uiChanged) {
        reloading = true;
        if (nextId) sessionStorage.setItem(bootStorageKey, nextId);
        const reason = uiChanged && !restarted ? 'UI updated — refreshing page…' : 'Server restarted — refreshing page…';
        window.DFlashStatusFeed?.note?.(reason);
        const bust = nextUi || Date.now();
        const url = new URL(window.location.href);
        url.searchParams.set('_ui', bust);
        window.setTimeout(() => window.location.replace(url.toString()), 500);
        return;
      }
      if (nextId) bootId = nextId;
      if (nextUi) uiVersion = nextUi;
      if (nextId) sessionStorage.setItem(bootStorageKey, nextId);
      offline = false;
      setConnectionBadge(true);
    } catch {
      offline = true;
      setConnectionBadge(false);
    } finally {
      window.clearTimeout(timeout);
      checking = false;
    }
  }

  function scheduleNextCheck() {
    if (reloading) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(async () => {
      timer = null;
      await check();
      scheduleNextCheck();
    }, POLL_MS);
  }

  function start() {
    setConnectionBadge(false);
    void check().finally(scheduleNextCheck);
  }

  document.addEventListener('DOMContentLoaded', start);
})();
