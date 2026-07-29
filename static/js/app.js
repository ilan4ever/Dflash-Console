const API = '';

let _config = null;
let _servers = [];
let _gpus = [];
let _pollTimer = null;

function $(id) {
  return document.getElementById(id);
}

function showToast(message, ok = true) {
  const el = $('toast');
  if (!el) return;
  el.textContent = message;
  el.className = `toast ${ok ? 'ok' : 'err'}`;
  el.classList.remove('hidden');
  window.clearTimeout(showToast._timer);
  showToast._timer = window.setTimeout(() => el.classList.add('hidden'), 3200);
}

async function api(path, options = {}) {
  const resp = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  try {
    data = await resp.json();
  } catch (_) {
    data = null;
  }
  if (!resp.ok) {
    const detail = data?.detail || data?.error || `HTTP ${resp.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function statusClass(status) {
  if (status === 'loaded') return 'status-loaded';
  if (status === 'running') return 'status-running';
  return 'status-stopped';
}

function gpuOptions(selected) {
  const sel = String(selected || 'auto');
  let html = '<option value="auto">Automatic</option>';
  for (const gpu of _gpus) {
    const val = String(gpu.index);
    html += `<option value="${val}"${sel === val ? ' selected' : ''}>${gpu.display_name || gpu.name}</option>`;
  }
  return html;
}

function switchView(view) {
  document.querySelectorAll('.nav-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === view);
  });
  ['dashboard', 'gpus', 'logs', 'settings'].forEach((name) => {
    const el = $(`view-${name}`);
    if (el) el.classList.toggle('hidden', name !== view);
  });
  if (view === 'logs') populateLogSelect();
}

function populateLogSelect() {
  const select = $('logServerSelect');
  if (!select) return;
  select.innerHTML = _servers.map((s) => `<option value="${s.id}">${s.label}</option>`).join('');
}

async function loadConfig() {
  const data = await api('/api/config');
  _config = data.config || {};
  const root = $('settingDflashRoot');
  const port = $('settingUiPort');
  if (root) root.value = _config.dflash_root || '';
  if (port) port.value = String(_config.ui_port || 8900);
}

async function refreshServers() {
  const data = await api('/api/servers');
  _servers = Array.isArray(data.servers) ? data.servers : [];
  _gpus = Array.isArray(data.gpus) ? data.gpus : _gpus;
  if (typeof renderServerGrid === 'function') renderServerGrid();
  if (typeof renderGpuList === 'function') renderGpuList();
}

async function refreshGpus() {
  const data = await api('/api/gpu-devices');
  _gpus = Array.isArray(data.gpus) ? data.gpus : [];
  if (typeof renderGpuList === 'function') renderGpuList();
}

async function saveGlobalSettings() {
  const payload = {
    dflash_root: $('settingDflashRoot')?.value?.trim(),
    ui_port: parseInt($('settingUiPort')?.value || '8900', 10),
  };
  await api('/api/config', { method: 'PUT', body: JSON.stringify(payload) });
  showToast('Settings saved');
  await loadConfig();
}

async function patchServer(serverId, patch) {
  await api(`/api/servers/${encodeURIComponent(serverId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

async function serverAction(serverId, action) {
  return api(`/api/servers/${encodeURIComponent(serverId)}/${action}`, { method: 'POST' });
}

async function loadLogs(serverId) {
  const data = await api(`/api/logs/${encodeURIComponent(serverId)}?tail=300`);
  const box = $('logBox');
  if (!box) return;
  const lines = Array.isArray(data.lines) ? data.lines : [];
  box.textContent = lines.length ? lines.join('\n') : 'No log output yet.';
  box.scrollTop = box.scrollHeight;
}

function startPolling() {
  if (_pollTimer) return;
  _pollTimer = window.setInterval(() => {
    void refreshServers();
  }, 12000);
}

document.addEventListener('DOMContentLoaded', async () => {
  document.querySelectorAll('.nav-btn').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view || 'dashboard'));
  });
  $('btnRefresh')?.addEventListener('click', () => void refreshServers());
  $('btnSaveSettings')?.addEventListener('click', () => void saveGlobalSettings().catch((e) => showToast(e.message, false)));
  $('btnRefreshLogs')?.addEventListener('click', () => {
    const id = $('logServerSelect')?.value;
    if (id) void loadLogs(id).catch((e) => showToast(e.message, false));
  });
  $('logServerSelect')?.addEventListener('change', (ev) => {
    const id = ev.target.value;
    if (id) void loadLogs(id);
  });

  try {
    await loadConfig();
    await refreshGpus();
    await refreshServers();
    startPolling();
  } catch (error) {
    showToast(`Failed to load: ${error.message}`, false);
    window.setTimeout(() => { void refreshServers(); }, 500);
  }
});

window.DFlashConsole = {
  api,
  showToast,
  patchServer,
  serverAction,
  refreshServers,
  gpuOptions,
  statusClass,
  get servers() { return _servers; },
  get gpus() { return _gpus; },
};
