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

  async function copyText(text) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      // fall through to legacy copy
    }
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return !!ok;
    } catch {
      return false;
    }
  }

  function openGithub(url) {
    const target = String(url || GITHUB_URL);
    try {
      const w = window.open(target, '_blank', 'noopener,noreferrer');
      if (w) return;
    } catch {
      // fall through
    }
    const a = document.createElement('a');
    a.href = target;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function offlineReportText(reproduction, userNote, reason) {
    return [
      '**DFlash Console bug report (generated offline: Console API unreachable)**',
      `Time: ${new Date().toISOString()}`,
      `Page: ${window.location.href}`,
      `User agent: ${navigator.userAgent}`,
      `API error: ${reason}`,
      '',
      '**Reproduction**',
      reproduction || '(none)',
      '',
      '**Actual behavior**',
      userNote || '(none)',
    ].join('\n');
  }

  async function submitReport() {
    const status = el('dfBugReportStatus');
    const btn = el('dfBugReportSubmit');
    const reproduction = String(el('dfBugReportRepro')?.value || '').trim();
    const userNote = String(el('dfBugReportActual')?.value || '').trim();
    const uploadPrivate = !!el('dfBugReportPrivateUpload')?.checked;
    if (btn) btn.disabled = true;
    if (status) status.textContent = 'Preparing diagnostic report...';
    let data = null;
    let apiError = '';
    try {
      data = await sendReport({ reproduction, user_note: userNote, upload_private: uploadPrivate });
    } catch (err) {
      apiError = err?.message || 'Console API unreachable';
    }
    try {
      let text = String(data?.clipboard_text || '');
      let githubUrl = data?.github_url;
      if (!text) {
        // API unreachable: still let the user file the issue with what we know client-side.
        text = offlineReportText(reproduction, userNote, apiError || 'empty report');
      } else {
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
          'Diagnostic report was copied to clipboard - paste below if needed (remove paths you do not want to share).',
        ].join('\n');
        text = `${summary}\n\n---\n\n${text}`;
      }
      const copied = await copyText(text);
      openGithub(githubUrl);
      const note = apiError ? ' (Console API was unreachable, so only a basic report was generated)' : '';
      const msg = copied
        ? (data?.uploaded ? 'Report copied. Private upload sent. GitHub opened.' : `Report copied to clipboard. GitHub opened.${note}`)
        : `GitHub opened, but the clipboard was blocked. Copy the text from the issue form manually.${note}`;
      if (status) status.textContent = msg;
      if (typeof toast === 'function') toast(msg, copied);
      if (copied || data) closeModal();
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
