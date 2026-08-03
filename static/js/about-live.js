/** Keep About metadata synchronized with the visible application version. */
(function () {
  function syncVersion() {
    const source = document.getElementById('dfAppVersion');
    const target = document.getElementById('dfAboutVersion');
    if (target) target.textContent = source?.textContent?.trim() || 'Development build';
  }

  document.addEventListener('DOMContentLoaded', syncVersion);
  window.DFlashAboutLive = { syncVersion };
})();
