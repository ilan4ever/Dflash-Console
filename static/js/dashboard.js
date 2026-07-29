function renderGpuList() {
  const list = document.getElementById('gpuList');
  if (!list) return;
  const gpus = window.DFlashConsole.gpus || [];
  if (!gpus.length) {
    list.innerHTML = '<div class="gpu-item">No NVIDIA GPUs detected.</div>';
    return;
  }
  list.innerHTML = gpus.map((gpu) => `
    <div class="gpu-item">
      <strong>GPU ${gpu.index}</strong> — ${gpu.display_name || gpu.name}
      ${gpu.vram_gb ? `<div style="color: var(--muted); font-size: 12px; margin-top: 4px;">${gpu.vram_gb} GB VRAM</div>` : ''}
    </div>
  `).join('');
}

function renderServerGrid() {
  const grid = document.getElementById('serverGrid');
  if (!grid) return;
  const servers = window.DFlashConsole.servers || [];
  if (!servers.length) {
    grid.innerHTML = '<div class="panel">No servers configured. Edit config.json to add profiles.</div>';
    return;
  }

  grid.innerHTML = servers.map((server) => {
    const status = server.status || 'stopped';
    const loaded = Array.isArray(server.loaded_models) && server.loaded_models.length
      ? server.loaded_models.join(', ')
      : 'None';
    return `
      <article class="card" data-server-id="${server.id}">
        <div class="card-header">
          <div>
            <div class="card-title">${escapeHtml(server.label || server.id)}</div>
            <div class="card-sub">${escapeHtml(server.profile || '')} · port ${server.port}</div>
          </div>
          <span class="status-pill ${window.DFlashConsole.statusClass(status)}">${status}</span>
        </div>

        <div class="meta-row">
          <div>
            <div class="label">Model</div>
            <div class="value">${escapeHtml(server.model_id || '—')}</div>
          </div>
          <div>
            <div class="label">Planned GPU</div>
            <div class="value">${escapeHtml(server.gpu_display || '—')}</div>
          </div>
          <div>
            <div class="label">Server URL</div>
            <div class="value mono">${escapeHtml(server.api_url || '')}</div>
          </div>
          <div>
            <div class="label">Loaded now</div>
            <div class="value">${escapeHtml(loaded)}</div>
          </div>
        </div>

        <div class="field-grid">
          <div class="field">
            <label>GPU</label>
            <select data-field="gpu_device">${window.DFlashConsole.gpuOptions(server.gpu_device)}</select>
          </div>
          <div class="field">
            <label>Context (tokens)</label>
            <input data-field="context_size" type="number" min="2048" max="131072" step="1024" value="${Number(server.context_size || 8192)}">
          </div>
          <div class="field">
            <label>Idle unload (minutes)</label>
            <input data-field="idle_unload_minutes" type="number" min="0" max="1440" step="1" value="${Number(server.idle_unload_minutes || 0)}">
          </div>
          <div class="field">
            <label>Profile</label>
            <select data-field="profile">
              ${profileOption('gemma-chat', server.profile)}
              ${profileOption('gemma-12-ar', server.profile)}
              ${profileOption('gemma-ar', server.profile)}
              ${profileOption('qwen-dflash', server.profile)}
              ${profileOption('qwen-ar', server.profile)}
              ${profileOption('bonsai', server.profile)}
              ${profileOption('bonsai-spec', server.profile)}
            </select>
          </div>
        </div>

        <div class="card-actions">
          <button class="btn btn-primary" data-action="start">Start</button>
          <button class="btn" data-action="reload">Reload</button>
          <button class="btn btn-danger" data-action="unload">Unload</button>
          <button class="btn" data-action="save">Save</button>
          <button class="btn" data-action="copy-url">Copy URL</button>
        </div>
      </article>
    `;
  }).join('');

  grid.querySelectorAll('.card').forEach((card) => bindCard(card));
}

function profileOption(value, selected) {
  const sel = String(selected || '') === value ? ' selected' : '';
  return `<option value="${value}"${sel}>${value}</option>`;
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function collectCardPatch(card) {
  const patch = {};
  card.querySelectorAll('[data-field]').forEach((el) => {
    const key = el.dataset.field;
    if (!key) return;
    if (el.type === 'number') {
      patch[key] = parseInt(el.value || '0', 10);
    } else {
      patch[key] = el.value;
    }
  });
  return patch;
}

function bindCard(card) {
  const serverId = card.dataset.serverId;
  if (!serverId) return;

  card.querySelectorAll('[data-action]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.action;
      try {
        if (action === 'save') {
          const patch = collectCardPatch(card);
          await window.DFlashConsole.patchServer(serverId, patch);
          window.DFlashConsole.showToast('Server settings saved');
          await window.DFlashConsole.refreshServers();
          return;
        }
        if (action === 'copy-url') {
          const url = card.querySelector('.mono')?.textContent?.trim();
          if (url) {
            await navigator.clipboard.writeText(url);
            window.DFlashConsole.showToast('API URL copied');
          }
          return;
        }
        if (action === 'reload') {
          const patch = collectCardPatch(card);
          await window.DFlashConsole.patchServer(serverId, patch);
          await window.DFlashConsole.serverAction(serverId, 'reload');
          window.DFlashConsole.showToast('Server reloaded');
        } else if (action === 'unload') {
          await window.DFlashConsole.serverAction(serverId, 'unload');
          window.DFlashConsole.showToast('Server unloaded');
        } else if (action === 'start') {
          const patch = collectCardPatch(card);
          await window.DFlashConsole.patchServer(serverId, patch);
          await window.DFlashConsole.serverAction(serverId, 'start');
          window.DFlashConsole.showToast('Server started');
        }
        await window.DFlashConsole.refreshServers();
      } catch (error) {
        window.DFlashConsole.showToast(error.message, false);
      }
    });
  });
}

window.renderServerGrid = renderServerGrid;
window.renderGpuList = renderGpuList;
