/** Desktop app settings — tray, startup, shell paths (Electron only). */
(function () {
  const desktop = () => window.DFlashDesktop;
  const { toast } = window.ConsoleApi || {};

  let saving = false;
  let current = null;
  let updateStatus = null;
  let installStarting = false;

  function el(id) {
    return document.getElementById(id);
  }

  function setChecked(id, value) {
    const input = el(id);
    if (input) input.checked = Boolean(value);
  }

  function syncDependentControls() {
    const minimize = el('appSettingsMinimizeTray')?.checked !== false;
    const startMin = el('appSettingsStartMinimized');
    if (!startMin) return;
    startMin.disabled = !minimize;
    if (!minimize) startMin.checked = false;
  }

  function applyToForm(data) {
    current = data || null;
    const isDesktop = Boolean(desktop()?.getAppSettings);
    el('appSettingsBrowserNotice')?.classList.toggle('hidden', isDesktop);
    el('appSettingsForm')?.classList.toggle('hidden', !isDesktop);
    if (el('appSettingsConsoleUrl')) {
      el('appSettingsConsoleUrl').textContent = data?.consoleUrl
        || `${window.location.protocol}//${window.location.host}/`;
    }

    if (!isDesktop || !data) return;

    setChecked('appSettingsMinimizeTray', data.minimizeToTray);
    setChecked('appSettingsStartMinimized', data.startMinimized);
    setChecked('appSettingsShowSplash', data.showSplashOnStartup);
    setChecked('appSettingsStartWithWindows', data.startWithWindows);
    setChecked('appSettingsAllowAutomaticUpdates', data.allowAutomaticUpdates !== false);
    if (el('appSettingsUpdateNotice')) {
      el('appSettingsUpdateNotice').value = data.updateNotice === 'header' ? 'header' : 'popup';
    }
    const startupStatus = el('appSettingsStartupStatus');
    if (startupStatus) {
      startupStatus.textContent = data.startWithWindows
        ? (data.startupRegistered
          ? 'Windows startup is active.'
          : 'Windows startup registration needs attention.')
        : 'Windows startup is off.';
    }
    syncDependentControls();

    if (el('appSettingsVersion')) {
      const version = data.appVersion || '—';
      const electron = data.electronVersion ? ` · Electron ${data.electronVersion}` : '';
      el('appSettingsVersion').textContent = `${version}${electron}`;
    }
    if (el('appSettingsDataRoot')) el('appSettingsDataRoot').textContent = data.dataRoot || '—';
    if (el('appSettingsUserData')) el('appSettingsUserData').textContent = data.userDataPath || '—';
    if (el('appSettingsConsoleUrl')) {
      el('appSettingsConsoleUrl').textContent = data.consoleUrl
        || `${window.location.protocol}//${window.location.host}/`;
    }
  }

  async function load() {
    const api = desktop();
    if (!api?.getAppSettings) {
      applyToForm(null);
      return;
    }
    try {
      const data = await api.getAppSettings();
      applyToForm(data);
    } catch (err) {
      toast?.('Could not load app settings.', 'error');
      applyToForm(null);
    }
  }

  function renderUpdateStatus(status) {
    updateStatus = status || null;
    const section = el('appUpdateSection');
    const check = el('appUpdateCheckBtn');
    const download = el('appUpdateDownloadBtn');
    const install = el('appUpdateInstallBtn');
    const title = el('appUpdateStatus');
    const detail = el('appUpdateDetail');
    const progress = el('appUpdateProgress');
    const progressBar = el('appUpdateProgressBar');
    const api = desktop();
    const configured = Boolean(api?.checkForUpdate);
    section?.classList.toggle('hidden', !configured);
    if (!configured) return;

    const state = String(status?.state || 'idle');
    const available = Boolean(status?.updateAvailable || status?.manifest);
    const ready = Boolean(status?.ready || state === 'ready');
    if (title) {
      title.textContent = state === 'checking'
        ? 'Checking for updates…'
        : ready
          ? `Version ${status.latestVersion || 'new'} is ready`
          : available
            ? `Version ${status.latestVersion || 'new'} is available`
            : state === 'error' ? 'Update check failed' : 'DFlash Console is up to date';
    }
    if (detail) detail.textContent = status?.error || status?.message || 'Updates are checked automatically in the background.';
    if (progress) {
      const percentage = Number(status?.percentage || (
        status?.totalBytes
          ? (Number(status.receivedBytes || 0) / Number(status.totalBytes)) * 100
          : 0
      ));
      if (progressBar) progressBar.value = Math.max(0, Math.min(100, percentage));
      progress.textContent = state === 'downloading' && status?.totalBytes
        ? `${Math.round(percentage)}%`
        : '';
    }
    if (check) check.disabled = state === 'checking' || state === 'downloading' || state === 'installing';
    download?.classList.toggle('hidden', !available || ready || state === 'downloading');
    install?.classList.toggle('hidden', !ready);
    renderUpdateNotice(status);
  }

  // Electron prefixes IPC errors with "Error invoking remote method '…': Error:".
  function plainError(err) {
    return String(err?.message || '').replace(/^Error invoking remote method '[^']*': (?:Error: )?/, '');
  }

  function updateVersion(status) {
    return String(status?.latestVersion || status?.manifest?.version || '').trim();
  }

  // Header notice: the installer was downloaded and verified in the
  // background, so "Upgrade now" starts the install straight away.
  function renderUpdateNotice(status) {
    const notice = el('appUpdateNotice');
    if (!notice) return;
    const state = String(status?.state || '');
    const installing = state === 'installing' || installStarting;
    const ready = Boolean(status?.ready || state === 'ready');
    notice.classList.toggle('hidden', !(ready || installing));
    const text = el('appUpdateNoticeText');
    const version = updateVersion(status);
    if (text) {
      text.textContent = installing
        ? 'Installing update…'
        : version ? `Version ${version} ready` : 'Update ready';
    }
    const go = el('appUpdateNoticeInstall');
    if (go) {
      go.disabled = installing;
      go.classList.toggle('hidden', installing);
    }
  }

  function setUpdatePromptOpen(open) {
    const modal = el('desktopUpdateModal');
    if (!modal) return;
    modal.classList.toggle('open', Boolean(open));
    modal.setAttribute('aria-hidden', open ? 'false' : 'true');
    document.body.classList.toggle('modal-open', Boolean(open));
  }

  // Opened from the header notice, never on its own: the separate pop-up
  // window (if the user keeps it on) is the only thing that appears by itself.
  function openUpdateDetails() {
    const status = updateStatus;
    if (!status?.ready) return;
    const version = updateVersion(status);
    const message = el('desktopUpdateMessage');
    const notes = el('desktopUpdateNotes');
    const title = el('desktopUpdateTitle');
    if (title) title.textContent = version ? `Version ${version} is ready` : 'A new version is ready';
    if (message) message.textContent = 'Downloaded and verified. DFlash Console closes, installs it, and reopens.';
    if (notes) notes.textContent = status.releaseNotes || '';
    notes?.classList.toggle('hidden', !status.releaseNotes);
    el('desktopUpdateModal')?.classList.remove('is-busy');
    el('desktopUpdateInstall')?.removeAttribute('disabled');
    setUpdatePromptOpen(true);
    el('desktopUpdateInstall')?.focus();
  }

  async function installNow() {
    const api = desktop();
    if (!api?.installUpdateNow || installStarting) return;
    installStarting = true;
    renderUpdateNotice(updateStatus);
    el('desktopUpdateInstall')?.setAttribute('disabled', 'disabled');
    el('desktopUpdateModal')?.classList.add('is-busy');
    try {
      await api.installUpdateNow();
    } catch (err) {
      installStarting = false;
      renderUpdateNotice(updateStatus);
      el('desktopUpdateModal')?.classList.remove('is-busy');
      el('desktopUpdateInstall')?.removeAttribute('disabled');
      toast?.(plainError(err) || 'The update could not be started. Try again from Settings → Desktop app.', 'error');
    }
  }

  async function checkForUpdate() {
    const api = desktop();
    if (!api?.checkForUpdate) return;
    try {
      renderUpdateStatus({ state: 'checking' });
      renderUpdateStatus(await api.checkForUpdate());
    } catch (err) {
      renderUpdateStatus({ state: 'error', error: err?.message || 'Could not check for updates.' });
    }
  }

  async function savePatch(patch) {
    const api = desktop();
    if (!api?.setAppSettings || saving) return;
    saving = true;
    try {
      const data = await api.setAppSettings(patch);
      applyToForm(data);
      toast?.('App settings saved.');
    } catch (err) {
      toast?.('Could not save app settings.', 'error');
      await load();
    } finally {
      saving = false;
    }
  }

  function bindCheckbox(id, key) {
    el(id)?.addEventListener('change', (event) => {
      syncDependentControls();
      const patch = { [key]: event.target.checked };
      if (key === 'minimizeToTray' && !event.target.checked) {
        patch.startMinimized = false;
      }
      void savePatch(patch);
    });
  }

  function bind() {
    bindCheckbox('appSettingsMinimizeTray', 'minimizeToTray');
    bindCheckbox('appSettingsStartMinimized', 'startMinimized');
    bindCheckbox('appSettingsShowSplash', 'showSplashOnStartup');
    bindCheckbox('appSettingsStartWithWindows', 'startWithWindows');
    bindCheckbox('appSettingsAllowAutomaticUpdates', 'allowAutomaticUpdates');

    el('appSettingsChooseDataRoot')?.addEventListener('click', async () => {
      const api = desktop();
      if (!api?.chooseDataRoot) return;
      try {
        const data = await api.chooseDataRoot();
        applyToForm(data);
        toast?.('Console data folder updated. Restart the app if engines do not reconnect.');
      } catch (err) {
        toast?.('Data folder was not changed.', 'error');
      }
    });

    el('appSettingsOpenUserData')?.addEventListener('click', () => {
      void desktop()?.openUserDataFolder?.();
    });

    el('appUpdateCheckBtn')?.addEventListener('click', () => void checkForUpdate());
    el('appUpdateDownloadBtn')?.addEventListener('click', async () => {
      try {
        renderUpdateStatus({ ...(updateStatus || {}), state: 'downloading' });
        renderUpdateStatus(await desktop()?.downloadUpdate?.());
      } catch (err) {
        renderUpdateStatus({ ...(updateStatus || {}), state: 'error', error: err?.message || 'Update download failed.' });
      }
    });
    el('appUpdateInstallBtn')?.addEventListener('click', async () => {
      try {
        await desktop()?.installUpdate?.();
      } catch (err) {
        renderUpdateStatus({ ...(updateStatus || {}), state: 'error', error: err?.message || 'Update installation failed.' });
      }
    });
    el('appSettingsUpdateNotice')?.addEventListener('change', (event) => {
      void savePatch({ updateNotice: event.target.value === 'header' ? 'header' : 'popup' });
    });
    el('appUpdateNoticeInfo')?.addEventListener('click', openUpdateDetails);
    el('appUpdateNoticeInstall')?.addEventListener('click', () => void installNow());
    el('desktopUpdateLater')?.addEventListener('click', () => setUpdatePromptOpen(false));
    el('desktopUpdateInstall')?.addEventListener('click', () => void installNow());
    desktop()?.onUpdateStatus?.(renderUpdateStatus);
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    if (document.body.dataset.activeView === 'settings'
      && document.querySelector('.lm-settings-panel.active')?.dataset.settingsPanel === 'app-settings') {
      void load();
    }
    void desktop()?.getUpdateStatus?.().then(renderUpdateStatus);
  });

  window.DFlashAppSettingsLive = {
    render: load,
  };
})();
