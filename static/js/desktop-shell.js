/** Keep the desktop shell version visible even when the API/UI runtime is older. */
(function () {
  async function syncShellVersion() {
    if (!window.DFlashDesktop?.getShellVersion) return;
    try {
      const version = String(await window.DFlashDesktop.getShellVersion() || '').trim();
      if (!version) return;
      const label = `v${version}`;
      const badge = document.getElementById('dfAppVersion');
      if (badge) {
        badge.textContent = label;
        badge.title = `DFlash Console shell ${label}`;
      }
      window.DFlashAboutLive?.syncVersion?.();
    } catch (_err) {
      // Keep the backend badge when Electron metadata is unavailable.
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    void syncShellVersion();
  });
})();
