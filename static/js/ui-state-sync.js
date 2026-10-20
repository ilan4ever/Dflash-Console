/**
 * Shared UI state between every client of the Console.
 *
 * localStorage is per client: the Electron window and a browser tab each have
 * their own copy, so a pin (or a load setting) made in one never appeared in
 * the other. Shared state now lives in the Console data root (`ui-state.json`)
 * and is read/written through `/api/ui-state`; localStorage stays only as an
 * instant-paint mirror so the first render does not wait for the network.
 *
 * Lists (pins): 'models' (Model library) and 'engines' (Engines page) - two
 * deliberately independent pin lists.
 * Settings (prefs): modelGpuDevices, loadEngine, modelPrefs - the per-model
 * choices that used to be trapped in one client's localStorage.
 */
(function () {
  const { api } = window.ConsoleApi;
  const SCOPES = ['models', 'engines'];
  const MIRROR_KEYS = {
    models: 'dflashConsole.pinnedModels',
    engines: 'dflashConsole.pinnedEngines',
  };
  const PREFS = ['modelGpuDevices', 'loadEngine', 'modelPrefs'];
  const PREF_MIRRORS = {
    modelGpuDevices: 'dflashConsole.modelGpuDevices',
    loadEngine: 'dflashConsole.loadEngine',
    modelPrefs: 'dflashConsole.modelPrefs',
  };
  const POLL_MS = 6000;
  // Set the first time this profile talks to the shared store. Before that,
  // localStorage may hold values that predate it (they get migrated once).
  const SYNCED_KEY = 'dflashConsole.uiStateSynced';

  const state = { models: [], engines: [], prefs: {} };
  const pendingScopes = new Set();
  const pendingPrefs = new Set();
  let readyPromise = null;
  let rev = 0;
  let pollTimer = null;
  let requestSeq = 0;

  function clientName() {
    return window.DFlashDesktop ? 'electron' : 'web';
  }

  function readMirror(scope) {
    try {
      const list = JSON.parse(localStorage.getItem(MIRROR_KEYS[scope]) || '[]');
      return Array.isArray(list) ? list.filter(Boolean).map(String) : [];
    } catch {
      return [];
    }
  }

  function writeMirror(scope, keys) {
    try {
      localStorage.setItem(MIRROR_KEYS[scope], JSON.stringify(keys));
    } catch {
      /* storage full/blocked — the server copy is authoritative */
    }
  }
  function readPrefMirror(name) {
    const storageKey = PREF_MIRRORS[name];
    if (!storageKey) return undefined;
    const raw = localStorage.getItem(storageKey);
    if (raw == null) return undefined;
    if (name === 'loadEngine') return raw;
    try {
      return JSON.parse(raw);
    } catch {
      return undefined;
    }
  }

  function writePrefMirror(name, value) {
    const storageKey = PREF_MIRRORS[name];
    if (!storageKey) return;
    try {
      if (value === undefined || value === null) localStorage.removeItem(storageKey);
      else localStorage.setItem(storageKey, name === 'loadEngine' ? String(value) : JSON.stringify(value));
    } catch {
      /* storage full/blocked - the server copy is authoritative */
    }
  }
  function normalize(list) {
    return Array.isArray(list) ? list.filter(Boolean).map(String) : [];
  }

  function sameSet(a, b) {
    if (a.length !== b.length) return false;
    const left = [...a].sort();
    const right = [...b].sort();
    return left.every((value, index) => value === right[index]);
  }

  function sameValue(a, b) {
    try {
      return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
    } catch {
      return false;
    }
  }

  function applyServerState(payload, { announce = true } = {}) {
    const pins = payload?.pins && typeof payload.pins === 'object' ? payload.pins : {};
    const hasPrefs = Boolean(payload?.prefs) && typeof payload.prefs === 'object';
    const prefs = hasPrefs ? payload.prefs : {};
    const changedScopes = [];
    const changedPrefs = [];
    SCOPES.forEach((scope) => {
      // Never clobber a scope with an in-flight local write.
      if (pendingScopes.has(scope)) return;
      const next = normalize(pins[scope]);
      if (sameSet(state[scope], next)) return;
      state[scope] = next;
      writeMirror(scope, next);
      changedScopes.push(scope);
    });
    if (hasPrefs) {
      PREFS.forEach((name) => {
        if (pendingPrefs.has(name)) return;
        const next = prefs[name];
        const known = Object.prototype.hasOwnProperty.call(state.prefs, name);
        if (next === undefined) {
          // Removed by the other client: drop the mirror too, or the stale
          // value would keep winning the fallback read.
          if (!known) return;
          delete state.prefs[name];
          writePrefMirror(name, undefined);
          changedPrefs.push(name);
          return;
        }
        if (sameValue(state.prefs[name], next)) return;
        state.prefs[name] = next;
        writePrefMirror(name, next);
        changedPrefs.push(name);
      });
    }
    const nextRev = Number(payload?.rev || 0);
    if (Number.isFinite(nextRev) && nextRev >= rev) rev = nextRev;
    try {
      localStorage.setItem(SYNCED_KEY, String(rev));
    } catch {
      /* ignore */
    }
    if (announce && (changedScopes.length || changedPrefs.length)) {
      window.dispatchEvent(new CustomEvent('dflash:ui-state', {
        detail: { scopes: changedScopes, prefs: changedPrefs, rev },
      }));
    }
    return { scopes: changedScopes, prefs: changedPrefs };
  }

  async function fetchState({ announce = true } = {}) {
    try {
      const payload = await api('/api/ui-state');
      return applyServerState(payload, { announce });
    } catch {
      return { scopes: [], prefs: [] };
    }
  }

  async function pushPref(name, value) {
    pendingPrefs.add(name);
    try {
      const payload = await api('/api/ui-state/prefs', {
        method: 'PUT',
        body: JSON.stringify({ name, value, client: clientName() }),
      });
      applyServerState(payload, { announce: false });
    } catch {
      /* local mirror keeps the change; the next change retries */
    } finally {
      pendingPrefs.delete(name);
    }
  }

  async function ensureReady() {
    if (!readyPromise) {
      readyPromise = (async () => {
        // Paint from the mirror immediately, then take the shared copy.
        SCOPES.forEach((scope) => {
          state[scope] = readMirror(scope);
        });
        PREFS.forEach((name) => {
          const mirrored = readPrefMirror(name);
          if (mirrored !== undefined) state.prefs[name] = mirrored;
        });
        const beforeScopes = { models: [...state.models], engines: [...state.engines] };
        const beforePrefs = { ...state.prefs };
        const legacyProfile = localStorage.getItem(SYNCED_KEY) == null;
        await fetchState({ announce: false });

        // One-time migration: values that predate the shared store get pushed
        // once, so an existing browser/Electron profile does not lose them.
        // After the first sync the server is authoritative - otherwise a value
        // deleted in the other client could be resurrected from a stale mirror.
        if (legacyProfile) {
          for (const scope of SCOPES) {
            if (state[scope].length || !beforeScopes[scope].length) continue;
            state[scope] = beforeScopes[scope];
            pendingScopes.add(scope);
            try {
              const payload = await api('/api/ui-state/pins', {
                method: 'PUT',
                body: JSON.stringify({ scope, keys: state[scope], client: clientName() }),
              });
              applyServerState(payload, { announce: false });
            } catch {
              /* keep the local list; the next change retries */
            } finally {
              pendingScopes.delete(scope);
            }
            writeMirror(scope, state[scope]);
          }
          for (const name of PREFS) {
            const serverValue = state.prefs[name];
            const localValue = beforePrefs[name];
            if (serverValue !== undefined || localValue === undefined) continue;
            await pushPref(name, localValue);
          }
        }
      })();
    }
    await readyPromise;
    return state;
  }

  function getPins(scope) {
    const list = state[scope];
    return Array.isArray(list) ? [...list] : readMirror(scope);
  }

  function getPref(name, fallback = undefined) {
    if (Object.prototype.hasOwnProperty.call(state.prefs, name)) return state.prefs[name];
    const mirrored = readPrefMirror(name);
    return mirrored === undefined ? fallback : mirrored;
  }

  async function setPref(name, value) {
    const known = PREFS.includes(name);
    state.prefs[name] = value;
    if (known) writePrefMirror(name, value);
    pendingPrefs.add(name);
    try {
      const payload = await api('/api/ui-state/prefs', {
        method: 'PUT',
        body: JSON.stringify({ name, value, client: clientName() }),
      });
      applyServerState(payload, { announce: false });
    } catch {
      /* the local mirror keeps the change; the next poll re-syncs */
    } finally {
      pendingPrefs.delete(name);
    }
  }

  async function setPins(scope, keys) {
    const next = normalize(keys);
    state[scope] = next;
    writeMirror(scope, next);
    pendingScopes.add(scope);
    const seq = ++requestSeq;
    try {
      const payload = await api('/api/ui-state/pins', {
        method: 'PUT',
        body: JSON.stringify({ scope, keys: next, client: clientName() }),
      });
      if (seq === requestSeq) applyServerState(payload, { announce: false });
    } catch {
      /* the local mirror keeps the change; the next poll re-syncs */
    } finally {
      pendingScopes.delete(scope);
    }
  }

  async function togglePin(scope, key) {
    const ident = String(key || '').trim();
    if (!ident) return false;
    const current = normalize(state[scope]);
    const has = current.includes(ident);
    const next = has ? current.filter((item) => item !== ident) : [...current, ident];
    await setPins(scope, next);
    return !has;
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void fetchState();
    }, POLL_MS);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') void fetchState();
    });
    window.addEventListener('focus', () => { void fetchState(); });
    // Two tabs of the SAME profile still update each other instantly.
    window.addEventListener('storage', (event) => {
      const scope = SCOPES.find((name) => MIRROR_KEYS[name] === event.key);
      if (scope && !pendingScopes.has(scope)) {
        const next = normalize(readMirror(scope));
        if (sameSet(state[scope], next)) return;
        state[scope] = next;
        window.dispatchEvent(new CustomEvent('dflash:ui-state', { detail: { scopes: [scope], prefs: [], rev } }));
        return;
      }
      const pref = PREFS.find((name) => PREF_MIRRORS[name] === event.key);
      if (!pref || pendingPrefs.has(pref)) return;
      const next = readPrefMirror(pref);
      if (next === undefined || sameValue(state.prefs[pref], next)) return;
      state.prefs[pref] = next;
      window.dispatchEvent(new CustomEvent('dflash:ui-state', { detail: { scopes: [], prefs: [pref], rev } }));
    });
  }

  window.DFlashUiState = {
    scopes: [...SCOPES],
    prefNames: [...PREFS],
    whenReady: ensureReady,
    getPins,
    setPins,
    togglePin,
    getPref,
    setPref,
    refresh: fetchState,
    get rev() {
      return rev;
    },
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      void ensureReady().then(startPolling);
    });
  } else {
    void ensureReady().then(startPolling);
  }
})();
