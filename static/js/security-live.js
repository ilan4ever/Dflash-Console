/**
 * Settings → Sign-in & keys: owner password, signed-in browsers, machine keys.
 * Backend: api/auth_routes.py (GET /api/security and friends).
 */
window.DFlashSecurityLive = (function () {
  const { api, toast } = window.ConsoleApi;
  let state = null;
  let loading = null;
  let bound = false;

  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatWhen(ts) {
    if (!ts) return 'never';
    try {
      return new Date(Number(ts) * 1000).toLocaleString();
    } catch (_) {
      return '—';
    }
  }

  function formatGb(bytes) {
    return `${(Number(bytes || 0) / (1024 ** 3)).toFixed(1)} GB`;
  }

  function shortAgent(agent) {
    const ua = String(agent || '');
    if (!ua) return '';
    const browser = /Edg\//.test(ua) ? 'Edge'
      : /Firefox\//.test(ua) ? 'Firefox'
        : /Chrome\//.test(ua) ? 'Chrome'
          : /Safari\//.test(ua) ? 'Safari'
            : 'Browser';
    const os = /Windows/.test(ua) ? 'Windows'
      : /Android/.test(ua) ? 'Android'
        : /iPhone|iPad/.test(ua) ? 'iOS'
          : /Mac OS X/.test(ua) ? 'macOS'
            : /Linux/.test(ua) ? 'Linux'
              : '';
    return os ? `${browser} on ${os}` : browser;
  }

  function setHidden(el, hidden) {
    if (el) el.classList.toggle('hidden', Boolean(hidden));
  }

  function showLoadError(message) {
    const el = $('securityLoadError');
    if (!el) return;
    el.textContent = message || '';
    setHidden(el, !message);
  }

  async function render() {
    if (loading) return loading;
    loading = api('/api/security', { timeoutMs: 15000 })
      .then((data) => {
        state = data || {};
        showLoadError('');
        paint();
      })
      .catch((err) => {
        showLoadError(err?.message || 'Could not load sign-in settings.');
      })
      .finally(() => {
        loading = null;
      });
    return loading;
  }

  function paint() {
    if (!state) return;
    const remote = state.local === false;
    const remoteNote = $('securityRemoteNote');
    if (remoteNote) {
      remoteNote.textContent = remote
        ? `You are signed in from ${state.client_ip || 'another computer'}. Some actions only work on the Console’s own PC.`
        : '';
      setHidden(remoteNote, !remote);
    }
    setHidden($('securitySignOutBtn'), !(remote && state.via === 'session'));
    paintPassword(remote);
    paintSessions();
    paintKeys();
  }

  function paintPassword(remote) {
    const info = state.password || {};
    const isSet = info.set === true;
    const status = $('securityPasswordStatus');
    if (status) {
      status.textContent = isSet
        ? `A password is set (since ${formatWhen(info.set_at)}). The owner can sign in with it from other computers.`
        : 'No password is set, so nobody can sign in from another computer. Programs on this PC do not need it.';
    }
    // On this PC the owner already has full access; another computer must prove it.
    setHidden($('securityCurrentPasswordField'), !(isSet && remote));
    const save = $('securitySavePasswordBtn');
    if (save) save.textContent = isSet ? 'Change password' : 'Set password';
    setHidden($('securityRemovePasswordBtn'), !isSet);
  }

  function paintSessions() {
    const list = $('securitySessionsList');
    if (!list) return;
    const rows = Array.isArray(state.sessions) ? state.sessions : [];
    if (!rows.length) {
      list.innerHTML = '<p class="lm-setting-desc">No browser is signed in from another computer.</p>';
    } else {
      list.innerHTML = rows.map((row) => `
        <article class="lm-security-row">
          <div class="lm-security-row-main">
            <div class="lm-security-row-title">
              <strong>${escapeHtml(row.ip || 'Unknown address')}</strong>
              <span class="lm-setting-desc">${escapeHtml(shortAgent(row.user_agent))}</span>
              ${row.current ? '<span class="lm-security-badge">This browser</span>' : ''}
            </div>
            <div class="lm-setting-desc">Signed in ${escapeHtml(formatWhen(row.created_at))} · last active ${escapeHtml(formatWhen(row.last_seen_at))}</div>
          </div>
          <div class="lm-security-row-actions">
            <button class="lm-btn ghost small" type="button" data-session-revoke="${escapeHtml(row.id)}">Sign out</button>
          </div>
        </article>`).join('');
    }
    const others = rows.filter((row) => !row.current).length;
    const revokeAll = $('securityRevokeSessionsBtn');
    if (revokeAll) revokeAll.disabled = others === 0;
  }

  function paintKeys() {
    const list = $('securityKeysList');
    if (!list) return;
    const rows = Array.isArray(state.keys) ? state.keys : [];
    if (!rows.length) {
      list.innerHTML = '<p class="lm-setting-desc">No machine keys yet.</p>';
      return;
    }
    list.innerHTML = rows.map((key) => {
      const used = key.last_used_at
        ? `last used ${formatWhen(key.last_used_at)}${key.last_used_ip ? ` from ${key.last_used_ip}` : ''}`
        : 'never used';
      const downloaded = Number(key.downloaded_bytes || 0);
      return `
        <article class="lm-security-row" data-key-id="${escapeHtml(key.id)}">
          <div class="lm-security-row-main">
            <div class="lm-security-row-title">
              <strong>${escapeHtml(key.name)}</strong>
              <code class="lm-security-hint">${escapeHtml(key.hint)}…</code>
            </div>
            <div class="lm-setting-desc">Created ${escapeHtml(formatWhen(key.created_at))} · ${escapeHtml(used)}</div>
            <label class="lm-security-check">
              <input type="checkbox" data-key-download ${key.may_download ? 'checked' : ''}>
              <span>May download models here</span>
            </label>
            <div class="lm-security-limit${key.may_download ? '' : ' hidden'}">
              <span class="lm-setting-desc">Hugging Face catalog files only, up to</span>
              <input class="lm-input lm-security-limit-input" type="number" min="1" max="100000" step="1"
                value="${escapeHtml(String(Math.round(Number(key.download_limit_gb || 0))))}" data-key-limit aria-label="Download limit in GB for ${escapeHtml(key.name)}">
              <span class="lm-setting-desc">GB · used ${escapeHtml(formatGb(downloaded))}</span>
              ${downloaded > 0 ? '<button class="lm-btn ghost small" type="button" data-key-reset>Reset</button>' : ''}
            </div>
          </div>
          <div class="lm-security-row-actions">
            <button class="lm-btn ghost small danger" type="button" data-key-revoke>Revoke</button>
          </div>
        </article>`;
    }).join('');
  }

  async function savePassword() {
    const next = $('securityNewPassword')?.value || '';
    const repeat = $('securityConfirmPassword')?.value || '';
    const minLength = Number(state?.password_min_length || 8);
    if (next.length < minLength) {
      toast(`Use at least ${minLength} characters.`, false, { report: false });
      return;
    }
    if (next !== repeat) {
      toast('The two new passwords are not the same.', false, { report: false });
      return;
    }
    const body = { new_password: next };
    const currentField = $('securityCurrentPasswordField');
    if (currentField && !currentField.classList.contains('hidden')) {
      body.current_password = $('securityCurrentPassword')?.value || '';
    }
    const wasSet = state?.password?.set === true;
    try {
      await api('/api/security/password', { method: 'PUT', body: JSON.stringify(body) });
      ['securityCurrentPassword', 'securityNewPassword', 'securityConfirmPassword'].forEach((id) => {
        const el = $(id);
        if (el) el.value = '';
      });
      toast(wasSet ? 'Password changed. Other browsers were signed out.' : 'Password set.');
      await render();
    } catch (err) {
      toast(err.message || 'Could not save the password.', false, { report: false });
    }
  }

  async function removePassword() {
    const ok = window.confirm(
      'Remove the owner password? Nobody will be able to sign in from another computer, and every signed-in browser is signed out. Machine keys keep working.',
    );
    if (!ok) return;
    const body = {};
    const currentField = $('securityCurrentPasswordField');
    if (currentField && !currentField.classList.contains('hidden')) {
      body.current_password = $('securityCurrentPassword')?.value || '';
    }
    try {
      await api('/api/security/password/remove', { method: 'POST', body: JSON.stringify(body) });
      toast('Password removed.');
      if (state?.local === false) {
        window.location.assign('/login');
        return;
      }
      await render();
    } catch (err) {
      toast(err.message || 'Could not remove the password.', false, { report: false });
    }
  }

  async function revokeSession(id) {
    try {
      await api(`/api/security/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (id && id === state?.current_session_id) {
        window.location.assign('/login');
        return;
      }
      toast('Browser signed out.');
      await render();
    } catch (err) {
      toast(err.message || 'Could not sign out that browser.', false, { report: false });
    }
  }

  async function revokeOtherSessions() {
    try {
      const data = await api('/api/security/sessions', { method: 'DELETE' });
      toast(data?.revoked ? `Signed out ${data.revoked} browser(s).` : 'No other browser was signed in.');
      await render();
    } catch (err) {
      toast(err.message || 'Could not sign out the other browsers.', false, { report: false });
    }
  }

  async function signOut() {
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } catch (_) {
      /* go to the sign-in page anyway */
    }
    window.location.assign('/login');
  }

  async function createKey() {
    const nameEl = $('securityKeyName');
    const name = (nameEl?.value || '').trim();
    if (!name) {
      toast('Name the key first, for example after the computer that will use it.', false, { report: false });
      nameEl?.focus();
      return;
    }
    try {
      const data = await api('/api/security/keys', { method: 'POST', body: JSON.stringify({ name }) });
      if (nameEl) nameEl.value = '';
      const box = $('securityNewKey');
      const value = $('securityNewKeyValue');
      if (value) value.textContent = data?.key || '';
      setHidden(box, !data?.key);
      await render();
    } catch (err) {
      toast(err.message || 'Could not create the key.', false, { report: false });
    }
  }

  async function copyNewKey() {
    const text = $('securityNewKeyValue')?.textContent || '';
    if (!text) return;
    try {
      // navigator.clipboard needs a secure context (this PC, or HTTPS).
      await navigator.clipboard.writeText(text);
      toast('Key copied.');
      return;
    } catch (_) {
      /* fall back to selecting the text */
    }
    const range = document.createRange();
    range.selectNodeContents($('securityNewKeyValue'));
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    let copied = false;
    try {
      copied = document.execCommand('copy');
    } catch (_) {
      copied = false;
    }
    toast(copied ? 'Key copied.' : 'Key selected. Press Ctrl+C to copy it.', true);
  }

  async function updateKey(id, patch, message) {
    try {
      await api(`/api/security/keys/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) });
      if (message) toast(message);
      await render();
    } catch (err) {
      toast(err.message || 'Could not update the key.', false, { report: false });
      await render();
    }
  }

  async function revokeKey(id, name) {
    const ok = window.confirm(`Revoke the key “${name || id}”? The Console that uses it can no longer use this GPU.`);
    if (!ok) return;
    try {
      await api(`/api/security/keys/${encodeURIComponent(id)}`, { method: 'DELETE' });
      toast('Key revoked.');
      await render();
    } catch (err) {
      toast(err.message || 'Could not revoke the key.', false, { report: false });
    }
  }

  function keyRow(target) {
    const row = target.closest('[data-key-id]');
    if (!row) return null;
    const id = row.dataset.keyId;
    const key = (state?.keys || []).find((item) => item.id === id) || null;
    return { row, id, key };
  }

  function bind() {
    if (bound) return;
    bound = true;
    $('securitySavePasswordBtn')?.addEventListener('click', () => void savePassword());
    $('securityRemovePasswordBtn')?.addEventListener('click', () => void removePassword());
    $('securityRevokeSessionsBtn')?.addEventListener('click', () => void revokeOtherSessions());
    $('securitySignOutBtn')?.addEventListener('click', () => void signOut());
    $('securityCreateKeyBtn')?.addEventListener('click', () => void createKey());
    $('securityKeyName')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') void createKey();
    });
    $('securityCopyKeyBtn')?.addEventListener('click', () => void copyNewKey());
    $('securitySessionsList')?.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-session-revoke]');
      if (btn) void revokeSession(btn.dataset.sessionRevoke);
    });
    const keys = $('securityKeysList');
    keys?.addEventListener('click', (event) => {
      const found = keyRow(event.target);
      if (!found) return;
      if (event.target.closest('[data-key-revoke]')) void revokeKey(found.id, found.key?.name);
      if (event.target.closest('[data-key-reset]')) void updateKey(found.id, { reset_usage: true }, 'Download usage reset.');
    });
    keys?.addEventListener('change', (event) => {
      const found = keyRow(event.target);
      if (!found) return;
      if (event.target.matches('[data-key-download]')) {
        const allowed = event.target.checked;
        void updateKey(
          found.id,
          { may_download: allowed },
          allowed ? 'This key may now download Hugging Face models here.' : 'This key can no longer download models here.',
        );
      } else if (event.target.matches('[data-key-limit]')) {
        const gb = Math.max(1, Math.min(100000, Math.round(Number(event.target.value) || 0)));
        void updateKey(found.id, { download_limit_gb: gb }, `Download limit set to ${gb} GB.`);
      }
    });
  }

  bind();
  return { render };
})();
