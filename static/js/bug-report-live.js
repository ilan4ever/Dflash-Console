/** One-click bug report: diagnostic bundle + GitHub issue (optional private upload). */
(function () {
  const { api, toast } = window.ConsoleApi || {};
  const GITHUB_URL = 'https://github.com/ilan4ever/Dflash-Console/issues/new?template=bug_report.yml';

  function el(id) {
    return document.getElementById(id);
  }

  function currentPageContext() {
    const hash = String(window.location.hash || '').replace(/^#\/?/, '').trim();
    const tab = hash.split('/')[0] || 'engines';
    return hash ? `Page: ${hash}` : `Tab: ${tab}`;
  }

  function openModal(options = {}) {
    const modal = el('dfBugReportModal');
    if (!modal) return;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    const status = el('dfBugReportStatus');
    if (status) status.textContent = '';
    if (el('dfBugReportActual')) {
      el('dfBugReportActual').value = String(options.userNote || '');
    }
    if (el('dfBugReportRepro')) {
      el('dfBugReportRepro').value = String(options.reproduction || '');
    }
    void checkPrivateUpload().then(() => {
      const box = el('dfBugReportPrivateUpload');
      if (box) box.checked = !!options.uploadPrivate;
    });
    window.setTimeout(() => el('dfBugReportActual')?.focus(), 0);
  }

  function closeModal() {
    const modal = el('dfBugReportModal');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  async function checkPrivateUpload() {
    const row = el('dfBugReportPrivateRow');
    const box = el('dfBugReportPrivateUpload');
    if (!row || !box || typeof api !== 'function') return false;
    try {
      const data = await api('/api/diagnostics/bundle?tail=50');
      const configured = data?.private_upload_configured === true;
      row.classList.toggle('hidden', !configured);
      box.checked = false;
      return configured;
    } catch {
      row.classList.add('hidden');
      return false;
    }
  }

  async function submitReport() {
    const status = el('dfBugReportStatus');
    const btn = el('dfBugReportSubmit');
    const reproduction = String(el('dfBugReportRepro')?.value || '').trim();
    const userNote = String(el('dfBugReportActual')?.value || '').trim();
    const uploadPrivate = !!el('dfBugReportPrivateUpload')?.checked;
    if (btn) btn.disabled = true;
    if (status) status.textContent = 'Preparing diagnostic report…';
    try {
      const data = await sendReport({ reproduction, user_note: userNote, upload_private: uploadPrivate });
      const text = String(data?.clipboard_text || '');
      if (!text) throw new Error('Empty diagnostic report');
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      }
      const prefill = data?.github_prefill || {};
      const summary = [
        `Report ID: ${data?.report_id || 'n/a'}`,
        `Version: ${prefill.version || ''}`,
        `Environment: ${prefill.environment || ''}`,
        '',
        '**Reproduction**',
        reproduction || prefill.reproduction || '(see clipboard diagnostic report)',
        '',
        '**Actual behavior**',
        userNote || prefill.actual || '',
        '',
        'Diagnostic report was copied to clipboard — paste below if needed (remove paths you do not want to share).',
      ].join('\n');
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(`${summary}\n\n---\n\n${text}`);
      }
      if (status) {
        status.textContent = data?.uploaded
          ? 'Report copied. Private upload sent. Opening GitHub…'
          : data?.upload_error && uploadPrivate
            ? `Report copied. Private upload failed: ${data.upload_error}`
            : 'Diagnostic report copied. Opening GitHub bug form…';
      }
      if (typeof toast === 'function') {
        toast(data?.uploaded ? 'Report copied and uploaded privately' : 'Diagnostic report copied to clipboard');
      }
      window.open(String(data?.github_url || GITHUB_URL), '_blank', 'noopener,noreferrer');
      closeModal();
    } catch (err) {
      if (status) status.textContent = err?.message || 'Could not prepare report';
      if (typeof toast === 'function') toast(err?.message || 'Could not prepare report', false);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function sendReport(payload) {
    if (typeof api !== 'function') throw new Error('Console API is unavailable');
    return api('/api/diagnostics/report', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async function quickReportFromError(message, context = '') {
    const userNote = String(message || '').trim();
    if (!userNote) return;
    const reproduction = String(context || currentPageContext()).trim();
    try {
      const configured = await checkPrivateUpload();
      if (configured) {
        const data = await sendReport({
          user_note: userNote,
          reproduction,
          upload_private: true,
        });
        if (data?.uploaded) {
          if (typeof toast === 'function') {
            toast(`Bug report sent (${data.report_id || 'ok'})`);
          }
          return;
        }
      }
    } catch {
      // Fall back to the full report dialog.
    }
    openModal({ userNote, reproduction, uploadPrivate: true });
  }

  function bind() {
    el('dfBugReportOpen')?.addEventListener('click', () => openModal());
    el('dfBugReportClose')?.addEventListener('click', closeModal);
    el('dfBugReportCancel')?.addEventListener('click', closeModal);
    el('dfBugReportSubmit')?.addEventListener('click', () => void submitReport());
    el('dfBugReportModal')?.querySelector('.lm-modal-backdrop')?.addEventListener('click', (e) => {
      if (e.target?.classList?.contains('lm-modal-backdrop')) closeModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && el('dfBugReportModal')?.classList.contains('open')) closeModal();
    });
  }

  document.addEventListener('DOMContentLoaded', bind);
  window.DFlashBugReport = { open: openModal, close: closeModal, quickReportFromError };
})();
