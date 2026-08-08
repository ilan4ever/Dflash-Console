/** DFlash Console — shell navigation & layout */
(function () {
  const views = document.querySelectorAll('.lm-view');
  const tabs = document.querySelectorAll('.lm-tab');
  const pageTitle = document.getElementById('dfPageTitle');
  const topTitle = document.querySelector('.lm-topnav-title');
  const bodyEl = document.querySelector('.lm-body');
  const inspector = document.querySelector('.lm-inspector');
  const inspectorToggleBtn = document.querySelector('[data-action="toggle-inspector"]');
  const pageActions = document.querySelector('.df-page-actions');

  const titles = {
    chat: 'Playground',
    server: 'Developer',
    models: '',
    devices: 'LM Link',
    docs: 'Docs',
    catalog: 'Hugging Face',
    settings: 'Preferences',
    about: 'About',
  };

  const pageTitles = {
    chat: 'Playground',
    server: 'Engines',
    models: 'Model library',
    devices: 'Remote nodes',
    docs: 'Documentation',
    catalog: 'Model catalog',
    settings: 'Settings',
    about: 'About DFlash Console',
  };

  const inspectorFor = new Set(['server', 'models']);
  const validTabs = new Set(['chat', 'server', 'models', 'devices', 'docs', 'catalog', 'settings', 'about']);

  const ROUTE_TO_TAB = {
    engines: 'server',
    server: 'server',
    developer: 'server',
    playground: 'chat',
    chat: 'chat',
    library: 'models',
    models: 'models',
    nodes: 'devices',
    devices: 'devices',
    docs: 'docs',
    catalog: 'catalog',
    settings: 'settings',
    about: 'about',
  };

  const TAB_TO_PATH = {
    server: '/engines',
    chat: '/playground',
    models: '/library',
    devices: '/nodes',
    docs: '/docs',
    catalog: '/catalog',
    settings: '/settings',
    about: '/about',
  };

  function onViewEnter(tab) {
    if (tab === 'docs') void window.DFlashDocsLive?.refresh?.();
    if (tab === 'server') void window.DFlashServerLive?.refresh?.(true);
    if (tab === 'settings') window.DFlashSettingsLive?.onViewEnter?.();
    if (tab === 'catalog') window.DFlashModelSearchLive?.onViewEnter?.();
    if (tab === 'chat') {
      void window.DFlashChatLive?.onViewEnter?.();
      void window.DFlashSpeakLive?.onViewEnter?.();
    }
    if (tab === 'about') window.DFlashAboutLive?.syncVersion?.();
  }

  function onViewLeave(tab) {
    if (tab === 'settings') window.DFlashSettingsLive?.onViewLeave?.();
  }

  let activeTab = 'server';
  let bootComplete = false;

  function layoutPrefs() {
    return window.DFlashUiLayout;
  }

  function settingsPanelFromRoute(segment) {
    if (!segment) return null;
    const panel = String(segment).trim();
    const resolved = panel === 'gw-network' || panel === 'gw-behavior' || panel === 'gw-preset'
      ? 'gw-engines'
      : panel;
    return document.querySelector(`.lm-settings-nav-item[data-settings-panel="${resolved}"]`) ? resolved : null;
  }

  function parseHashRoute() {
    const hash = (location.hash || '').replace(/^#\/?/, '');
    if (!hash) return null;
    const parts = hash.split('/').filter(Boolean);
    const segment = (parts[0] || '').toLowerCase();
    const tab = ROUTE_TO_TAB[segment];
    if (!tab || !validTabs.has(tab)) return null;
    const settingsPanel = tab === 'settings' ? settingsPanelFromRoute(parts[1]) : null;
    return { tab, settingsPanel };
  }

  function syncHash() {
    if (!bootComplete) return;
    let path = TAB_TO_PATH[activeTab] || '/engines';
    if (activeTab === 'settings') {
      const panel = window.DFlashSettingsLive?.activePanelId?.()
        || layoutPrefs()?.getString?.('settings_panel');
      if (panel) path += `/${panel}`;
    }
    const next = `#${path}`;
    if (location.hash !== next) {
      history.replaceState(null, '', `${location.pathname}${location.search}${next}`);
    }
  }

  function setView(name, { persist = true, settingsPanel = null } = {}) {
    const tab = String(name || 'server');
    if (!validTabs.has(tab)) return;
    if (tab !== activeTab) onViewLeave(activeTab);
    activeTab = tab;
    views.forEach((v) => v.classList.toggle('active', v.dataset.view === tab));
    tabs.forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
    const heading = pageTitles[tab] || 'DFlash Console';
    if (pageTitle) pageTitle.textContent = heading;
    if (topTitle) {
      const label = titles[tab] || '';
      topTitle.textContent = label;
      topTitle.classList.toggle('hidden', !label);
    }
    if (pageActions) pageActions.style.display = inspectorFor.has(tab) ? '' : 'none';
    if (inspector) inspector.classList.toggle('hidden', !inspectorFor.has(tab));
    document.body.dataset.activeView = tab;
    if (tab === 'settings') {
      const panel = settingsPanel
        || layoutPrefs()?.getString?.('settings_panel')
        || 'app-settings';
      window.DFlashSettingsLive?.showPanel?.(panel, { persist: false });
    }
    if (persist) layoutPrefs()?.setString?.('active_view', tab);
    onViewEnter(tab);
    syncHash();
  }

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => setView(tab.dataset.tab));
  });

  function syncSysbarHeightVar() {
    const sysbar = document.querySelector('.lm-sysbar');
    if (!sysbar) return;
    const height = Math.ceil(sysbar.getBoundingClientRect().height);
    if (height > 0) {
      document.documentElement.style.setProperty('--df-sysbar-height', `${height}px`);
    }
  }

  function openModal(el) {
    if (!el) return;
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
  }

  function closeModal(el) {
    if (!el) return;
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) {
      document.body.classList.remove('modal-open');
    }
  }

  document.querySelectorAll('[data-action="close-modal"]').forEach((btn) => {
    btn.addEventListener('click', () => closeModal(btn.closest('.lm-modal')));
  });

  document.querySelectorAll('.lm-modal-backdrop').forEach((bd) => {
    bd.addEventListener('click', (e) => {
      if (e.target === bd) closeModal(bd.closest('.lm-modal'));
    });
  });

  document.querySelectorAll('.lm-modal-dialog').forEach((dialog) => {
    dialog.addEventListener('click', (e) => e.stopPropagation());
  });

  function setInspectorCollapsed(collapsed, { persist = true } = {}) {
    if (!bodyEl) return;
    bodyEl.classList.toggle('inspector-collapsed', collapsed);
    if (persist) bodyEl.dataset.userToggledInspector = '1';
    inspectorToggleBtn?.classList.toggle('active', !collapsed);
    if (persist) {
      layoutPrefs()?.setBool?.('inspector_collapsed', collapsed);
      try {
        localStorage.setItem('dflashConsole.inspectorCollapsed', collapsed ? '1' : '0');
      } catch {
        /* ignore */
      }
    }
  }

  if (inspectorToggleBtn && bodyEl) {
    inspectorToggleBtn.addEventListener('click', () => {
      setInspectorCollapsed(!bodyEl.classList.contains('inspector-collapsed'));
    });
  }

  document.querySelectorAll('.lm-inspector-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      const id = tab.dataset.inspectorTab;
      window.DFlashServerLive?.rememberInspectorTab?.(id);
      document.querySelectorAll('.lm-inspector-tab').forEach((t) => t.classList.toggle('active', t === tab));
      document.querySelectorAll('.lm-inspector-panel').forEach((p) => {
        p.classList.toggle('active', p.dataset.inspectorPanel === id);
      });
    });
  });

  document.querySelectorAll('[data-dropdown-trigger]').forEach((trigger) => {
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      const menu = trigger.closest('.lm-dropdown')?.querySelector('.lm-dropdown-menu');
      if (!menu) return;
      const willOpen = !menu.classList.contains('open');
      document.querySelectorAll('.lm-dropdown-menu.open').forEach((m) => m.classList.remove('open'));
      if (willOpen) menu.classList.add('open');
    });
  });

  document.addEventListener('click', () => {
    document.querySelectorAll('.lm-dropdown-menu.open').forEach((m) => m.classList.remove('open'));
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.lm-modal.open').forEach((modal) => closeModal(modal));
      document.getElementById('modelMetadataModal')?.classList.remove('open');
      document.querySelectorAll('.lm-dropdown-menu.open').forEach((m) => m.classList.remove('open'));
    }
  });

  function inspectorCollapsedPreference() {
    try {
      const local = localStorage.getItem('dflashConsole.inspectorCollapsed');
      if (local === '1') return true;
      if (local === '0') return false;
    } catch {
      /* ignore */
    }
    if (layoutPrefs()?.has?.('inspector_collapsed')) {
      return layoutPrefs().getBool('inspector_collapsed', false);
    }
    return layoutPrefs()?.getBool?.('inspector_collapsed', false) === true;
  }

  function fitLayout() {
    if (!bodyEl) return;
    const narrow = window.innerWidth < 960;
    if (narrow) {
      setInspectorCollapsed(true, { persist: false });
      return;
    }
    const hasPref = (() => {
      try {
        return localStorage.getItem('dflashConsole.inspectorCollapsed') != null;
      } catch {
        return false;
      }
    })() || layoutPrefs()?.has?.('inspector_collapsed');
    if (hasPref) {
      bodyEl.dataset.userToggledInspector = '1';
      setInspectorCollapsed(inspectorCollapsedPreference(), { persist: false });
    }
  }

  window.addEventListener('resize', () => {
    fitLayout();
    syncSysbarHeightVar();
  });

  window.addEventListener('hashchange', () => {
    if (!bootComplete) return;
    const route = parseHashRoute();
    if (!route) return;
    setView(route.tab, { persist: true, settingsPanel: route.settingsPanel });
  });

  syncSysbarHeightVar();
  window.syncSysbarHeightVar = syncSysbarHeightVar;

  window.DFlashShell = { setView, openModal, closeModal, syncHash };

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function setupLogsPanelLayout() {
    const serverView = document.querySelector('.lm-view[data-view="server"]');
    const dock = document.getElementById('serverLogsDock');
    const handle = document.getElementById('serverLogsResizeHandle');
    const hideBtn = document.getElementById('serverLogsHide');
    const restoreBtn = document.getElementById('serverLogsRestore');
    if (!serverView || !dock || !handle) return;

    const storedHeight = layoutPrefs()?.getNumber?.('logs_height');
    if (Number.isFinite(storedHeight) && storedHeight >= 120) {
      serverView.style.setProperty('--logs-height', `${storedHeight}px`);
    }

    if (layoutPrefs()?.getBool?.('logs_hidden')) {
      serverView.classList.add('logs-collapsed');
    }

    const logsMin = 120;
    const logsMax = () => clamp(Math.floor(window.innerHeight * 0.75), logsMin, 900);

    const startLogsResize = (clientY) => {
      const startY = clientY;
      const startH = dock.getBoundingClientRect().height;
      document.body.classList.add('lm-resizing-logs');

      const onMove = (ev) => {
        const next = clamp(startH + (startY - ev.clientY), logsMin, logsMax());
        serverView.style.setProperty('--logs-height', `${next}px`);
      };

      const onUp = () => {
        document.body.classList.remove('lm-resizing-logs');
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        const height = Math.round(dock.getBoundingClientRect().height);
        layoutPrefs()?.setNumber?.('logs_height', height);
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    };

    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startLogsResize(e.clientY);
    });

    handle.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault();
      const current = dock.getBoundingClientRect().height;
      const delta = e.key === 'ArrowUp' ? 16 : -16;
      const next = clamp(current + delta, logsMin, logsMax());
      serverView.style.setProperty('--logs-height', `${next}px`);
      layoutPrefs()?.setNumber?.('logs_height', next);
    });

    hideBtn?.addEventListener('click', () => {
      serverView.classList.add('logs-collapsed');
      layoutPrefs()?.setBool?.('logs_hidden', true);
    });

    restoreBtn?.addEventListener('click', () => {
      serverView.classList.remove('logs-collapsed');
      layoutPrefs()?.setBool?.('logs_hidden', false);
    });
  }

  function setupInspectorResize() {
    const handle = document.getElementById('inspectorResizeHandle');
    if (!bodyEl || !handle || !inspector) return;

    const storedWidth = layoutPrefs()?.getNumber?.('inspector_width');
    if (Number.isFinite(storedWidth) && storedWidth >= 220) {
      bodyEl.style.setProperty('--inspector-width', `${storedWidth}px`);
    }

    const widthMin = 220;
    const widthMax = () => clamp(Math.floor(window.innerWidth * 0.55), widthMin, 560);

    const startInspectorResize = (clientX) => {
      const startX = clientX;
      const startW = inspector.getBoundingClientRect().width;
      document.body.classList.add('lm-resizing-inspector');

      const onMove = (ev) => {
        const next = clamp(startW + (startX - ev.clientX), widthMin, widthMax());
        bodyEl.style.setProperty('--inspector-width', `${next}px`);
      };

      const onUp = () => {
        document.body.classList.remove('lm-resizing-inspector');
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        const width = Math.round(inspector.getBoundingClientRect().width);
        layoutPrefs()?.setNumber?.('inspector_width', width);
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    };

    handle.addEventListener('mousedown', (e) => {
      if (bodyEl.classList.contains('inspector-collapsed')) return;
      e.preventDefault();
      startInspectorResize(e.clientX);
    });

    handle.addEventListener('keydown', (e) => {
      if (bodyEl.classList.contains('inspector-collapsed')) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const current = inspector.getBoundingClientRect().width;
      const delta = e.key === 'ArrowLeft' ? 16 : -16;
      const next = clamp(current + delta, widthMin, widthMax());
      bodyEl.style.setProperty('--inspector-width', `${next}px`);
      layoutPrefs()?.setNumber?.('inspector_width', next);
    });
  }

  function setSidenavCollapsed(collapsed) {
    document.body.classList.toggle('df-sidenav-collapsed', collapsed);
    document.getElementById('sidenavRestoreBtn')?.classList.toggle('hidden', !collapsed);
    if (collapsed) {
      document.body.style.setProperty('--sidenav-width', '0px');
    } else {
      const storedWidth = layoutPrefs()?.getNumber?.('sidenav_width');
      if (Number.isFinite(storedWidth) && storedWidth >= 140) {
        setSidenavWidth(storedWidth);
      } else {
        document.body.style.removeProperty('--sidenav-width');
      }
    }
    layoutPrefs()?.setBool?.('sidenav_hidden', collapsed);
  }

  function setupSidenavCollapse() {
    const collapseBtn = document.getElementById('sidenavCollapseBtn');
    const restoreBtn = document.getElementById('sidenavRestoreBtn');
    if (!collapseBtn || !restoreBtn) return;

    const applyStored = () => {
      if (layoutPrefs()?.getBool?.('sidenav_hidden')) {
        setSidenavCollapsed(true);
      }
    };

    collapseBtn.addEventListener('click', () => setSidenavCollapsed(true));
    restoreBtn.addEventListener('click', () => setSidenavCollapsed(false));
    applyStored();
  }

  function setSidenavWidth(px) {
    document.body.style.setProperty('--sidenav-width', `${px}px`);
  }

  function setupSidenavResize() {
    const sidenav = document.querySelector('.df-sidenav');
    const handle = document.getElementById('sidenavResizeHandle');
    if (!sidenav || !handle) return;

    const storedWidth = layoutPrefs()?.getNumber?.('sidenav_width');
    if (Number.isFinite(storedWidth) && storedWidth >= 140) {
      setSidenavWidth(storedWidth);
    }

    const widthMin = 140;
    const widthMax = () => clamp(Math.floor(window.innerWidth * 0.42), widthMin, 360);

    const startSidenavResize = (clientX) => {
      if (window.innerWidth <= 820) return;
      if (document.body.classList.contains('df-sidenav-collapsed')) return;
      const startX = clientX;
      const startW = sidenav.getBoundingClientRect().width;
      document.body.classList.add('lm-resizing-sidenav');

      const onMove = (ev) => {
        const x = ev.clientX ?? ev.touches?.[0]?.clientX;
        if (x == null) return;
        if (ev.cancelable) ev.preventDefault();
        const next = clamp(startW + (x - startX), widthMin, widthMax());
        setSidenavWidth(next);
      };

      const onUp = () => {
        document.body.classList.remove('lm-resizing-sidenav');
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        window.removeEventListener('touchmove', onMove);
        window.removeEventListener('touchend', onUp);
        const width = Math.round(sidenav.getBoundingClientRect().width);
        layoutPrefs()?.setNumber?.('sidenav_width', width);
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
      window.addEventListener('touchmove', onMove, { passive: false });
      window.addEventListener('touchend', onUp);
    };

    handle.addEventListener('mousedown', (e) => {
      if (window.innerWidth <= 820) return;
      e.preventDefault();
      startSidenavResize(e.clientX);
    });

    handle.addEventListener('touchstart', (e) => {
      if (window.innerWidth <= 820) return;
      const touch = e.touches[0];
      if (!touch) return;
      e.preventDefault();
      startSidenavResize(touch.clientX);
    }, { passive: false });

    handle.addEventListener('keydown', (e) => {
      if (window.innerWidth <= 820) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const current = sidenav.getBoundingClientRect().width;
      const delta = e.key === 'ArrowRight' ? 16 : -16;
      const next = clamp(current + delta, widthMin, widthMax());
      setSidenavWidth(next);
      layoutPrefs()?.setNumber?.('sidenav_width', next);
    });
  }

  function applyInspectorFromConfig() {
    if (!bodyEl) return;
    const hasPref = (() => {
      try {
        return localStorage.getItem('dflashConsole.inspectorCollapsed') != null;
      } catch {
        return false;
      }
    })() || layoutPrefs()?.has?.('inspector_collapsed');
    if (!hasPref) return;
    bodyEl.dataset.userToggledInspector = '1';
    setInspectorCollapsed(inspectorCollapsedPreference(), { persist: false });
  }

  function restoreInitialView() {
    const fromHash = parseHashRoute();
    let tab = fromHash?.tab;
    let settingsPanel = fromHash?.settingsPanel;
    if (!tab) {
      tab = layoutPrefs()?.getString?.('active_view') || 'server';
    }
    if (!validTabs.has(tab)) tab = 'server';
    if (tab === 'settings' && !settingsPanel) {
      settingsPanel = layoutPrefs()?.getString?.('settings_panel') || null;
    }
    setView(tab, { persist: !fromHash, settingsPanel });
    if (!fromHash) syncHash();
  }

  function bootLayoutControls() {
    const ready = layoutPrefs()?.whenReady?.() ?? Promise.resolve();
    ready.then(() => {
      setupLogsPanelLayout();
      setupInspectorResize();
      setupSidenavResize();
      setupSidenavCollapse();
      applyInspectorFromConfig();
      const inspectorTab = layoutPrefs()?.getString?.('inspector_tab');
      if (inspectorTab === 'info' || inspectorTab === 'load') {
        window.DFlashServerLive?.focusInspectorTab?.(inspectorTab);
      }
      fitLayout();
      restoreInitialView();
      bootComplete = true;
      syncHash();
    });
  }

  bootLayoutControls();
})();
