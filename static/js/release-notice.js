/** Show a note when GitHub has a newer release than this running server. */
(function () {
  const { api } = window.ConsoleApi || {};

  function banner() {
    return document.getElementById('dfReleaseNotice');
  }

  function runningInDesktopApp() {
    return Boolean(window.DFlashDesktop);
  }

  function showReleaseNotice(data) {
    const el = banner();
    const text = document.getElementById('dfReleaseNoticeText');
    const link = document.getElementById('dfReleaseNoticeLink');
    if (!el || !text) return;
    if (runningInDesktopApp() || !data?.update_available || !data.message) {
      el.classList.add('hidden');
      return;
    }
    text.textContent = data.message;
    if (link && data.release_url) link.href = data.release_url;
    el.classList.remove('hidden');
  }

  async function checkReleaseNotice() {
    if (!api || runningInDesktopApp()) return;
    try {
      const data = await api('/api/release-notice', { timeoutMs: 12000 });
      showReleaseNotice(data);
    } catch {
      /* stay quiet when GitHub cannot be reached */
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    void checkReleaseNotice();
    window.setInterval(() => { void checkReleaseNotice(); }, 60 * 60 * 1000);
  });

  window.DFlashReleaseNotice = { show: showReleaseNotice, check: checkReleaseNotice };
})();
