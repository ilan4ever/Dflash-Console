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
  };

  const pageTitles = {
    chat: 'Playground',
    server: 'Inference engines',
    models: 'Model library',
    devices: 'Remote nodes',
    docs: 'Documentation',
    catalog: 'Model catalog',
    settings: 'Settings',
  };

  const inspectorFor = new Set(['server', 'models']);
  const validTabs = new Set(['chat', 'server', 'models', 'devices', 'docs', 'catalog', 'settings']);

  function onViewEnter(tab) {
    if (tab === 'docs') void window.DFlashDocsLive?.refresh?.();
    if (tab === 'server') void window.DFlashServerLive?.refresh?.(true);
    if (tab === 'settings') window.DFlashSettingsLive?.onViewEnter?.();
    if (tab === 'catalog') window.DFlashModelSearchLive?.onViewEnter?.();
    if (tab === 'chat') void window.DFlashChatLive?.onViewEnter?.();
  }

  function onViewLeave(tab) {
    if (tab === 'settings') window.DFlashSettingsLive?.onViewLeave?.();
  }

  let activeTab = 'server';

  function setView(name, { persist = true } = {}) {
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
    if (persist) localStorage.setItem('dflashConsole.activeTab', tab);
    onViewEnter(tab);
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

  if (inspectorToggleBtn && bodyEl) {
    inspectorToggleBtn.addEventListener('click', () => {
      bodyEl.classList.toggle('inspector-collapsed');
      bodyEl.dataset.userToggledInspector = '1';
      inspectorToggleBtn.classList.toggle('active', !bodyEl.classList.contains('inspector-collapsed'));
    });
  }

  document.querySelectorAll('.lm-inspector-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      const id = tab.dataset.inspectorTab;
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

  function fitLayout() {
    if (!bodyEl) return;
    const narrow = window.innerWidth < 960;
    if (narrow) {
      bodyEl.classList.add('inspector-collapsed');
      if (inspectorToggleBtn) inspectorToggleBtn.classList.remove('active');
    } else if (!bodyEl.dataset.userToggledInspector) {
      bodyEl.classList.remove('inspector-collapsed');
      if (inspectorToggleBtn) inspectorToggleBtn.classList.add('active');
    }
  }

  window.addEventListener('resize', () => {
    fitLayout();
    syncSysbarHeightVar();
  });
  fitLayout();
  syncSysbarHeightVar();
  window.syncSysbarHeightVar = syncSysbarHeightVar;

  const savedTab = localStorage.getItem('dflashConsole.activeTab');
  setView(validTabs.has(savedTab) ? savedTab : 'server', { persist: false });

  window.DFlashShell = { setView, openModal, closeModal };

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

    const storedHeight = parseInt(localStorage.getItem('dflashConsole.logsHeight') || '', 10);
    if (Number.isFinite(storedHeight) && storedHeight >= 120) {
      serverView.style.setProperty('--logs-height', `${storedHeight}px`);
    }

    if (localStorage.getItem('dflashConsole.logsHidden') === '1') {
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
        localStorage.setItem('dflashConsole.logsHeight', String(height));
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
      localStorage.setItem('dflashConsole.logsHeight', String(next));
    });

    hideBtn?.addEventListener('click', () => {
      serverView.classList.add('logs-collapsed');
      localStorage.setItem('dflashConsole.logsHidden', '1');
    });

    restoreBtn?.addEventListener('click', () => {
      serverView.classList.remove('logs-collapsed');
      localStorage.setItem('dflashConsole.logsHidden', '0');
    });
  }

  function setupInspectorResize() {
    const handle = document.getElementById('inspectorResizeHandle');
    if (!bodyEl || !handle || !inspector) return;

    const storedWidth = parseInt(localStorage.getItem('dflashConsole.inspectorWidth') || '', 10);
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
        localStorage.setItem('dflashConsole.inspectorWidth', String(width));
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
      localStorage.setItem('dflashConsole.inspectorWidth', String(next));
    });
  }

  setupLogsPanelLayout();
  setupInspectorResize();
})();
