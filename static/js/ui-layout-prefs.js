/** Persist panel/table resize values in config.json via /api/config */
(function () {
  const { api } = window.ConsoleApi;
  const LEGACY_KEYS = {
    sidenav_width: 'dflashConsole.sidenavWidth',
    inspector_width: 'dflashConsole.inspectorWidth',
    logs_height: 'dflashConsole.logsHeight',
    hf_search_left_width: 'dflashConsole.hfSearchLeftWidth',
    inspector_collapsed: 'dflashConsole.inspectorCollapsed',
  };
  const LEGACY_TABLE_PREFIX = 'dflashConsole.tableCols.';

  let cache = {};
  let readyPromise = null;
  let saveTimer = null;
  let dirty = false;

  function readLegacyInt(storageKey) {
    const raw = localStorage.getItem(storageKey);
    const value = parseInt(raw || '', 10);
    return Number.isFinite(value) ? value : null;
  }

  function readLegacyBool(storageKey) {
    const raw = localStorage.getItem(storageKey);
    if (raw === '1') return true;
    if (raw === '0') return false;
    return null;
  }

  function migrateFromLocalStorage() {
    let changed = false;
    Object.entries(LEGACY_KEYS).forEach(([key, storageKey]) => {
      if (key === 'inspector_collapsed') {
        if (cache[key] != null) return;
        const value = readLegacyBool(storageKey);
        if (value == null) return;
        cache[key] = value;
        changed = true;
        return;
      }
      if (cache[key] != null) return;
      const value = readLegacyInt(storageKey);
      if (value == null) return;
      cache[key] = value;
      changed = true;
    });
    if (localStorage.getItem('dflashConsole.logsHidden') === '1' && cache.logs_hidden !== true) {
      cache.logs_hidden = true;
      changed = true;
    }
    const legacyTab = localStorage.getItem('dflashConsole.activeTab');
    if (!cache.active_view && legacyTab) {
      cache.active_view = legacyTab;
      changed = true;
    }
    const legacyInspectorTab = localStorage.getItem('dflashConsole.inspectorTab');
    if (!cache.inspector_tab && (legacyInspectorTab === 'info' || legacyInspectorTab === 'load')) {
      cache.inspector_tab = legacyInspectorTab;
      changed = true;
    }
    if (!cache.table_columns || typeof cache.table_columns !== 'object') {
      cache.table_columns = {};
    }
    Object.keys(localStorage).forEach((storageKey) => {
      if (!storageKey.startsWith(LEGACY_TABLE_PREFIX)) return;
      const tableKey = storageKey.slice(LEGACY_TABLE_PREFIX.length);
      if (!tableKey || cache.table_columns[tableKey]) return;
      try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
        if (saved && typeof saved === 'object') {
          cache.table_columns[tableKey] = saved;
          changed = true;
        }
      } catch {
        /* ignore */
      }
    });
    if (changed) scheduleSave();
  }

  async function load() {
    if (!readyPromise) {
      readyPromise = (async () => {
        try {
          const data = await api('/api/config');
          cache = data.config?.ui_layout && typeof data.config.ui_layout === 'object'
            ? { ...data.config.ui_layout }
            : {};
        } catch {
          cache = {};
        }
        if (!cache.table_columns || typeof cache.table_columns !== 'object') {
          cache.table_columns = {};
        }
        migrateFromLocalStorage();
      })();
    }
    await readyPromise;
    return cache;
  }

  function whenReady() {
    return load();
  }

  function getNumber(key, fallback = null) {
    const value = cache[key];
    return Number.isFinite(value) ? value : fallback;
  }

  function getBool(key, fallback = false) {
    if (!(key in cache)) {
      const legacyKey = LEGACY_KEYS[key];
      if (legacyKey) {
        const legacy = readLegacyBool(legacyKey);
        if (legacy != null) return legacy;
      }
    }
    if (key in cache) return cache[key] === true;
    return fallback;
  }

  function getString(key, fallback = '') {
    const value = cache[key];
    return typeof value === 'string' && value ? value : fallback;
  }

  function has(key) {
    return Object.prototype.hasOwnProperty.call(cache, key);
  }

  function setString(key, value) {
    if (typeof value !== 'string' || !value) return;
    cache[key] = value;
    scheduleSave();
  }

  function getTableColumns(tableKey) {
    const cols = cache.table_columns?.[tableKey];
    return cols && typeof cols === 'object' ? { ...cols } : null;
  }

  function setNumber(key, value) {
    if (!Number.isFinite(value)) return;
    cache[key] = Math.round(value);
    scheduleSave();
  }

  function setBool(key, value) {
    cache[key] = value === true;
    const legacyKey = LEGACY_KEYS[key];
    if (legacyKey) {
      try {
        localStorage.setItem(legacyKey, value === true ? '1' : '0');
      } catch {
        /* ignore */
      }
    }
    scheduleSave();
  }

  function setTableColumns(tableKey, payload) {
    if (!tableKey || !payload || typeof payload !== 'object') return;
    if (!cache.table_columns || typeof cache.table_columns !== 'object') {
      cache.table_columns = {};
    }
    cache.table_columns[tableKey] = { ...payload };
    scheduleSave();
  }

  function scheduleSave() {
    dirty = true;
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      void flush();
    }, 450);
  }

  async function flush() {
    if (!dirty) return;
    dirty = false;
    try {
      await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify({ ui_layout: cache }),
      });
    } catch {
      dirty = true;
    }
  }

  window.DFlashUiLayout = {
    whenReady,
    getNumber,
    getBool,
    getString,
    has,
    getTableColumns,
    setNumber,
    setBool,
    setString,
    setTableColumns,
    flush,
  };

  window.addEventListener('beforeunload', () => { void flush(); });
  window.addEventListener('pagehide', () => { void flush(); });
})();
