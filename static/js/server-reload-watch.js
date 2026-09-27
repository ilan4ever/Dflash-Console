/** Reload the page when the Console API restarts or static UI files change. */
(function () {
  const POLL_MS = 5000;
  const HEALTH_TIMEOUT_MS = 2500;

  let bootId = null;
  let uiVersion = null;
  let connectionState = 'starting';
  let hasConnected = false;
  let reloading = false;
  let checking = false;
  let timer = null;
  let pendingReloadUrl = '';
  let reloadFocusHandler = null;
  let badgeEl = null;
  let devBadgeEl = null;
  let urlEl = null;
  const bootStorageKey = 'dflashConsole.lastSeenBootId';

  function badge() {
    if (!badgeEl) badgeEl = document.getElementById('serverLinkBadge');
    return badgeEl;
  }

  function devBadge() {
    if (!devBadgeEl) devBadgeEl = document.getElementById('devServerBadge');
    return devBadgeEl;
  }

  function consoleUrlEl() {
    if (!urlEl) urlEl = document.getElementById('dfConsoleUrl');
    return urlEl;
  }

  function consoleUrlText() {
    return `${window.location.protocol}//${window.location.host}/`;
  }

  function setConsoleUrl(health) {
    const el = consoleUrlEl();
    if (!el) return;
    const url = consoleUrlText();
    el.textContent = url;
    const root = String(health?.process_root || health?.console_root || '').trim();
    const configPath = String(health?.config_path || '').trim();
    el.title = [
      'This Console’s web address — same in the browser and the desktop app.',
      root ? `Data folder: ${root}` : '',
      configPath ? `Config: ${configPath}` : '',
      'Click to copy.',
    ].filter(Boolean).join('\n');
  }

  function copyConsoleUrl() {
    const url = consoleUrlText();
    const done = () => window.DFlashStatusFeed?.note?.(`Copied ${url}`);
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(done).catch(() => {});
      return;
    }
    done();
  }

  // Shown only when the health endpoint reports this is the developer server
  // (i.e. the UI is being served from the git checkout, not the installed app).
  function setDeveloperBadge(isDev) {
    const el = devBadge();
    if (!el) return;
    el.classList.toggle('hidden', !isDev);
  }

  function setConnectionBadge(state) {
    const el = badge();
    if (!el) return;
    const nextState = state === 'online' || state === 'offline' ? state : 'starting';
    const labels = { starting: 'Starting…', online: 'Online', offline: 'Offline' };
    const titles = {
      starting: 'Starting the Console API and checking engine services…',
      online: 'Console API connected',
      offline: 'Console API unreachable — start or restart the server',
    };
    connectionState = nextState;
    el.textContent = labels[nextState];
    el.classList.toggle('starting', nextState === 'starting');
    el.classList.toggle('online', nextState === 'online');
    el.classList.toggle('offline', nextState === 'offline');
    el.title = titles[nextState];
  }

  function formControlFocused() {
    return Boolean(document.activeElement?.matches?.(
      'select, input, textarea, button, [contenteditable="true"]',
    ));
  }

  function reloadWhenSafe(url) {
    const attempt = () => {
      if (formControlFocused()) {
        pendingReloadUrl = url;
        if (!reloadFocusHandler) {
          reloadFocusHandler = () => {
            if (formControlFocused() || !pendingReloadUrl) return;
            const nextUrl = pendingReloadUrl;
            pendingReloadUrl = '';
            document.removeEventListener('focusout', reloadFocusHandler, true);
            reloadFocusHandler = null;
            window.location.replace(nextUrl);
          };
          document.addEventListener('focusout', reloadFocusHandler, true);
        }
        return;
      }
      window.location.replace(url);
    };
    window.setTimeout(attempt, 500);
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
      setDeveloperBadge(Boolean(data?.dev_server));
      setConsoleUrl(data);
      const storedBootId = sessionStorage.getItem(bootStorageKey) || '';
      // The first successful health check belongs to the current page. A stale
      // boot id in sessionStorage must not trigger a startup page reload.
      const restarted = hasConnected && ((bootId && nextId && nextId !== bootId)
        || (storedBootId && nextId && nextId !== storedBootId));
      const uiChanged = uiVersion && nextUi && nextUi !== uiVersion;
      const recoveredAfterOffline = hasConnected && connectionState === 'offline' && nextId;
      setConnectionBadge('online');
      if (restarted || recoveredAfterOffline || uiChanged) {
        reloading = true;
        if (nextId) sessionStorage.setItem(bootStorageKey, nextId);
        const reason = uiChanged && !restarted ? 'UI updated — refreshing page…' : 'Server restarted — refreshing page…';
        window.DFlashStatusFeed?.note?.(reason);
        const bust = nextUi || Date.now();
        const url = new URL(window.location.href);
        url.searchParams.set('_ui', bust);
        reloadWhenSafe(url.toString());
        return;
      }
      if (nextId) bootId = nextId;
      if (nextUi) uiVersion = nextUi;
      if (nextId) sessionStorage.setItem(bootStorageKey, nextId);
      hasConnected = true;
      setConnectionBadge('online');
    } catch {
      setConnectionBadge(hasConnected ? 'offline' : 'starting');
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
    setConsoleUrl(null);
    consoleUrlEl()?.addEventListener('click', copyConsoleUrl);
    setConnectionBadge('starting');
    void check().finally(scheduleNextCheck);
  }

  document.addEventListener('DOMContentLoaded', start);
})();
