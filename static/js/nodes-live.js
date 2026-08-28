/** Remote inference nodes — register, health-check, secure connect wizard, and test chat proxy. */
(function () {
  const { api, toast } = window.ConsoleApi;

  let nodes = [];
  let pollTimer = null;
  let refreshInFlight = null;
  let connectWizard = null;
  let connectMethod = '';
  let connectSshScenario = 'reach_remote';
  let nodeContextTarget = null;

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function statusLabel(node) {
    const status = String(node.status || 'unknown').toLowerCase();
    if (status === 'online') return 'Online';
    if (status === 'offline') return 'Offline';
    if (status === 'disabled') return 'Disabled';
    if (status === 'error') return 'Error';
    return 'Unknown';
  }

  function statusClass(node) {
    const status = String(node.status || 'unknown').toLowerCase();
    if (status === 'online') return 'is-online';
    if (status === 'offline') return 'is-offline';
    if (status === 'disabled') return 'is-disabled';
    return 'is-error';
  }

  function formatChecked(node) {
    const ts = Number(node.checked_at || 0);
    if (!ts) return 'Not checked yet';
    try {
      return new Date(ts * 1000).toLocaleString();
    } catch {
      return 'Checked';
    }
  }

  function renderNodes() {
    const list = document.getElementById('nodesList');
    if (!list) return;
    if (!nodes.length) {
      list.innerHTML = `
        <div class="lm-nodes-empty">
          <p class="lm-setting-desc">No remote nodes yet. Use <strong>Connect securely</strong> for Tailscale or SSH, or add a LAN URL manually.</p>
        </div>`;
      return;
    }
    list.innerHTML = nodes.map((node) => {
      const version = node.remote_version ? ` · v${escapeHtml(node.remote_version)}` : '';
      const latency = node.latency_ms != null ? `${escapeHtml(String(node.latency_ms))} ms` : '—';
      const err = node.error ? `<div class="lm-nodes-error">${escapeHtml(node.error)}</div>` : '';
      return `
        <article class="lm-node-card" data-node-id="${escapeHtml(node.id)}">
          <div class="lm-node-card-main">
            <div class="lm-node-card-title">
              <strong>${escapeHtml(node.label || node.id)}</strong>
              <span class="lm-node-status ${statusClass(node)}">${escapeHtml(statusLabel(node))}</span>
            </div>
            <code class="lm-settings-path">${escapeHtml(node.base_url || '')}</code>
            <div class="lm-node-card-meta">
              Last check · ${escapeHtml(formatChecked(node))} · ${latency}${version}
              ${node.has_token ? ' · Token set' : ''}
            </div>
            ${err}
          </div>
          <div class="lm-node-card-actions">
            <button class="lm-btn ghost small" type="button" data-node-action="health" data-node-id="${escapeHtml(node.id)}">Check</button>
            <button class="lm-btn ghost small" type="button" data-node-action="chat" data-node-id="${escapeHtml(node.id)}">Test chat</button>
            <button class="lm-btn ghost small danger" type="button" data-node-action="remove" data-node-id="${escapeHtml(node.id)}">Remove</button>
          </div>
        </article>`;
    }).join('');
  }

  async function refreshNodes({ fresh = false } = {}) {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = api(`/api/nodes${fresh ? '?fresh=1' : ''}`, { timeoutMs: fresh ? 30000 : 15000 })
      .then((data) => {
        nodes = Array.isArray(data.nodes) ? data.nodes : [];
        renderNodes();
        return data;
      })
      .catch((err) => {
        const list = document.getElementById('nodesList');
        if (list) {
          list.innerHTML = `<p class="lm-setting-desc lm-settings-load-err">${escapeHtml(err.message || 'Could not load nodes')}</p>`;
        }
        throw err;
      })
      .finally(() => {
        refreshInFlight = null;
      });
    return refreshInFlight;
  }

  function openModal(id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
  }

  function closeModal(id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.lm-modal.open')) {
      document.body.classList.remove('modal-open');
    }
  }

  function openAddModal() {
    openModal('nodeAddModal');
    document.getElementById('nodeAddLabel').value = '';
    document.getElementById('nodeAddUrl').value = '';
    document.getElementById('nodeAddToken').value = '';
    document.getElementById('nodeAddLabel')?.focus();
  }

  function closeAddModal() {
    closeModal('nodeAddModal');
  }

  function copyText(text) {
    navigator.clipboard.writeText(String(text || '')).then(() => toast('Copied'));
  }

  function renderConnectMethodPicker() {
    return `
      <p class="lm-setting-desc">Pick an encrypted path to another DFlash Console. Both options keep traffic off the open internet.</p>
      <div class="lm-connect-method-grid">
        <button class="lm-connect-method-card" type="button" data-connect-pick="tailscale">
          <span class="lm-connect-badge">Recommended</span>
          <strong>Tailscale</strong>
          <span class="lm-setting-desc">Private mesh VPN. Install on both PCs, sign in to the same account, then use the tailnet IP.</span>
        </button>
        <button class="lm-connect-method-card" type="button" data-connect-pick="ssh">
          <span class="lm-connect-badge">Advanced</span>
          <strong>SSH tunnel</strong>
          <span class="lm-setting-desc">Encrypted port forwarding through a server you control. Good when Tailscale is not an option.</span>
        </button>
      </div>`;
  }

  function renderTailscalePanel() {
    const ts = connectWizard?.tailscale || {};
    const uiPort = Number(connectWizard?.ui_port || 8900);
    const peers = Array.isArray(ts.peers) ? ts.peers : [];
    const peerOptions = peers.map((peer) => {
      const label = `${peer.hostname || peer.ipv4}${peer.online ? '' : ' (offline)'}`;
      return `<option value="${escapeHtml(peer.ipv4)}">${escapeHtml(label)}</option>`;
    }).join('');
    const statusClass = ts.self_ip ? 'is-ok' : (ts.installed ? 'is-warn' : 'is-warn');
    const statusText = ts.self_ip
      ? `This PC is on Tailscale at ${ts.self_ip}. Other devices can reach this Console at ${connectWizard?.tailscale?.suggested_share_url || ''}.`
      : (ts.error || 'Install Tailscale on both PCs and sign in with the same account.');
    return `
      <button class="lm-btn ghost small" type="button" data-connect-back>← Back</button>
      <div class="lm-connect-panel">
        <div class="lm-connect-status ${statusClass}">${escapeHtml(statusText)}</div>
        <ol class="lm-connect-steps">
          <li>Install Tailscale on both PCs from <a href="${escapeHtml(ts.install_url || 'https://tailscale.com/download')}" target="_blank" rel="noopener">tailscale.com/download</a>.</li>
          <li>Sign in with the same tailnet on each machine.</li>
          <li>On the remote PC, confirm DFlash Console is running (port ${uiPort}).</li>
          <li>Pick the remote device below, test the URL, then add it as a node.</li>
        </ol>
        <label class="lm-field">
          <span>Remote device</span>
          <select class="lm-input" id="nodeConnectPeer">
            <option value="">Enter IP manually</option>
            ${peerOptions}
          </select>
        </label>
        <label class="lm-field">
          <span>Remote Console URL</span>
          <input class="lm-input" id="nodeConnectUrl" type="url" placeholder="http://100.x.x.x:${uiPort}" autocomplete="off">
        </label>
        <label class="lm-field">
          <span>Node name</span>
          <input class="lm-input" id="nodeConnectLabel" type="text" placeholder="Office GPU PC" autocomplete="off">
        </label>
        <label class="lm-field">
          <span>API token (optional)</span>
          <input class="lm-input" id="nodeConnectToken" type="password" placeholder="Shared secret" autocomplete="off">
        </label>
        <div class="lm-connect-test-result" id="nodeConnectTestResult"></div>
      </div>`;
  }

  function renderSshPanel() {
    const uiPort = Number(connectWizard?.ui_port || 8900);
    const bindPort = Number(connectWizard?.ssh?.default_local_bind_port || uiPort + 1);
    const reachActive = connectSshScenario === 'reach_remote';
    return `
      <button class="lm-btn ghost small" type="button" data-connect-back>← Back</button>
      <div class="lm-connect-panel">
        <p class="lm-setting-desc">SSH wraps the Console connection in an encrypted tunnel. Keep the terminal window open while the tunnel runs.</p>
        <div class="lm-connect-scenario-tabs">
          <button class="lm-btn ghost small ${reachActive ? 'is-active' : ''}" type="button" data-connect-scenario="reach_remote">Reach a remote Console</button>
          <button class="lm-btn ghost small ${!reachActive ? 'is-active' : ''}" type="button" data-connect-scenario="share_local">Share this Console</button>
        </div>
        <label class="lm-field">
          <span>SSH user</span>
          <input class="lm-input" id="nodeConnectSshUser" type="text" value="user" autocomplete="off">
        </label>
        <label class="lm-field">
          <span>SSH host</span>
          <input class="lm-input" id="nodeConnectSshHost" type="text" placeholder="remote.example.com" autocomplete="off">
        </label>
        <label class="lm-field ${reachActive ? '' : 'hidden'}">
          <span>Local port on this PC</span>
          <input class="lm-input" id="nodeConnectLocalPort" type="number" min="1024" max="65535" value="${bindPort}">
        </label>
        <label class="lm-field">
          <span>Remote Console port</span>
          <input class="lm-input" id="nodeConnectRemotePort" type="number" min="1" max="65535" value="${uiPort}">
        </label>
        <div class="lm-connect-command">
          <span class="lm-setting-desc" id="nodeConnectSshSummary">${escapeHtml(connectWizard?.ssh?.reach_remote?.summary || '')}</span>
          <pre id="nodeConnectSshCommand">${escapeHtml(connectWizard?.ssh?.reach_remote?.command || '')}</pre>
          <button class="lm-btn ghost small" type="button" id="nodeConnectCopyCmd">Copy command</button>
        </div>
        <ul class="lm-connect-steps" id="nodeConnectSshNotes">
          ${(connectWizard?.ssh?.reach_remote?.notes || []).map((line) => `<li>${escapeHtml(line)}</li>`).join('')}
        </ul>
        <label class="lm-field">
          <span>Node URL to register here</span>
          <input class="lm-input" id="nodeConnectUrl" type="url" value="http://127.0.0.1:${bindPort}" autocomplete="off">
          <span class="lm-setting-desc" id="nodeConnectUrlHint">After the tunnel is running, use this URL on this PC.</span>
        </label>
        <label class="lm-field">
          <span>Node name</span>
          <input class="lm-input" id="nodeConnectLabel" type="text" placeholder="Remote via SSH" autocomplete="off">
        </label>
        <label class="lm-field">
          <span>API token (optional)</span>
          <input class="lm-input" id="nodeConnectToken" type="password" placeholder="Shared secret" autocomplete="off">
        </label>
        <div class="lm-connect-test-result" id="nodeConnectTestResult"></div>
      </div>`;
  }

  function renderConnectFoot() {
    const foot = document.getElementById('nodeConnectFoot');
    if (!foot) return;
    if (!connectMethod) {
      foot.innerHTML = `<button class="lm-btn ghost" type="button" data-action="close-modal">Close</button>`;
      return;
    }
    foot.innerHTML = `
      <button class="lm-btn ghost" type="button" data-connect-back>Back</button>
      <button class="lm-btn ghost" type="button" id="nodeConnectTestBtn">Test connection</button>
      <button class="lm-btn primary" type="button" id="nodeConnectAddBtn">Add node</button>`;
  }

  function renderConnectWizard() {
    const body = document.getElementById('nodeConnectBody');
    if (!body) return;
    if (!connectMethod) {
      body.innerHTML = renderConnectMethodPicker();
    } else if (connectMethod === 'tailscale') {
      body.innerHTML = renderTailscalePanel();
      const peerSelect = document.getElementById('nodeConnectPeer');
      const urlInput = document.getElementById('nodeConnectUrl');
      if (peerSelect && urlInput) {
        peerSelect.addEventListener('change', () => {
          const ip = peerSelect.value.trim();
          if (ip) urlInput.value = `http://${ip}:${Number(connectWizard?.ui_port || 8900)}`;
        });
        if (peerSelect.options.length === 2) {
          peerSelect.selectedIndex = 1;
          peerSelect.dispatchEvent(new Event('change'));
        }
      }
    } else {
      body.innerHTML = renderSshPanel();
      void refreshSshCommand();
    }
    renderConnectFoot();
  }

  async function refreshSshCommand() {
    if (connectMethod !== 'ssh') return;
    const user = document.getElementById('nodeConnectSshUser')?.value?.trim() || 'user';
    const host = document.getElementById('nodeConnectSshHost')?.value?.trim() || '';
    const localPort = Number(document.getElementById('nodeConnectLocalPort')?.value || connectWizard?.ssh?.default_local_bind_port || 8901);
    const remotePort = Number(document.getElementById('nodeConnectRemotePort')?.value || connectWizard?.ui_port || 8900);
    if (!host) return;
    try {
      const data = await api('/api/nodes/connect/ssh-command', {
        method: 'POST',
        body: JSON.stringify({
          scenario: connectSshScenario,
          ssh_user: user,
          ssh_host: host,
          local_bind_port: localPort,
          remote_console_port: remotePort,
        }),
      });
      document.getElementById('nodeConnectSshCommand').textContent = data.command || '';
      const summary = document.getElementById('nodeConnectSshSummary');
      if (summary) summary.textContent = data.summary || '';
      const notes = document.getElementById('nodeConnectSshNotes');
      if (notes) notes.innerHTML = (data.notes || []).map((line) => `<li>${escapeHtml(line)}</li>`).join('');
      const urlInput = document.getElementById('nodeConnectUrl');
      if (urlInput && data.node_url_for_remote_operator) {
        urlInput.value = data.node_url_for_remote_operator;
      }
      const hint = document.getElementById('nodeConnectUrlHint');
      if (hint) {
        hint.textContent = connectSshScenario === 'share_local'
          ? 'Use this URL on the machine that connects through the SSH server.'
          : 'After the tunnel is running on this PC, register this local URL as the node.';
      }
    } catch (err) {
      toast(err.message || 'Could not build SSH command', false);
    }
  }

  async function openConnectModal() {
    connectMethod = '';
    connectSshScenario = 'reach_remote';
    openModal('nodeConnectModal');
    const body = document.getElementById('nodeConnectBody');
    if (body) body.innerHTML = '<p class="lm-setting-desc">Loading connection options…</p>';
    renderConnectFoot();
    try {
      connectWizard = await api('/api/nodes/connect/wizard');
      renderConnectWizard();
    } catch (err) {
      if (body) {
        body.innerHTML = `<p class="lm-setting-desc lm-settings-load-err">${escapeHtml(err.message || 'Could not load secure connect wizard')}</p>`;
      }
    }
  }

  async function testConnectUrl() {
    const baseUrl = document.getElementById('nodeConnectUrl')?.value?.trim() || '';
    const apiToken = document.getElementById('nodeConnectToken')?.value?.trim() || '';
    const result = document.getElementById('nodeConnectTestResult');
    if (!baseUrl) {
      toast('Console URL is required', false);
      return;
    }
    if (result) {
      result.className = 'lm-connect-test-result';
      result.textContent = 'Testing connection…';
    }
    try {
      const data = await api('/api/nodes/connect/test', {
        method: 'POST',
        body: JSON.stringify({ base_url: baseUrl, api_token: apiToken || undefined }),
        timeoutMs: 20000,
      });
      if (result) {
        result.className = `lm-connect-test-result ${data.online ? 'is-ok' : 'is-error'}`;
        result.textContent = data.online
          ? `Connected · v${data.remote_version || '?'} · ${data.latency_ms || '?'} ms`
          : (data.error || 'Console is not reachable at that URL');
      }
      if (data.online) toast('Connection OK');
      else toast(data.error || 'Connection failed', false);
    } catch (err) {
      if (result) {
        result.className = 'lm-connect-test-result is-error';
        result.textContent = err.message || 'Connection failed';
      }
      toast(err.message || 'Connection failed', false);
    }
  }

  async function addConnectNode() {
    const label = document.getElementById('nodeConnectLabel')?.value?.trim() || '';
    const baseUrl = document.getElementById('nodeConnectUrl')?.value?.trim() || '';
    const apiToken = document.getElementById('nodeConnectToken')?.value?.trim() || '';
    if (!label || !baseUrl) {
      toast('Name and Console URL are required', false);
      return;
    }
    const btn = document.getElementById('nodeConnectAddBtn');
    if (btn) btn.disabled = true;
    try {
      await api('/api/nodes', {
        method: 'POST',
        body: JSON.stringify({
          label,
          base_url: baseUrl,
          api_token: apiToken || undefined,
        }),
      });
      closeModal('nodeConnectModal');
      toast('Node added');
      await refreshNodes({ fresh: true });
    } catch (err) {
      toast(err.message || 'Could not add node', false);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function saveNode() {
    const label = document.getElementById('nodeAddLabel')?.value?.trim() || '';
    const baseUrl = document.getElementById('nodeAddUrl')?.value?.trim() || '';
    const apiToken = document.getElementById('nodeAddToken')?.value?.trim() || '';
    if (!label || !baseUrl) {
      toast('Name and Console URL are required', false);
      return;
    }
    const btn = document.getElementById('nodeAddSaveBtn');
    if (btn) btn.disabled = true;
    try {
      await api('/api/nodes', {
        method: 'POST',
        body: JSON.stringify({
          label,
          base_url: baseUrl,
          api_token: apiToken || undefined,
        }),
      });
      closeAddModal();
      toast('Node added');
      await refreshNodes({ fresh: true });
    } catch (err) {
      toast(err.message || 'Could not add node', false);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function checkNode(nodeId) {
    try {
      await api(`/api/nodes/${encodeURIComponent(nodeId)}/health`, { method: 'POST', timeoutMs: 20000 });
      toast('Health check complete');
      await refreshNodes({ fresh: false });
    } catch (err) {
      toast(err.message || 'Health check failed', false);
      await refreshNodes({ fresh: false });
    }
  }

  async function testChat(nodeId) {
    const node = nodes.find((row) => row.id === nodeId);
    if (!node) return;
    try {
      const resp = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'remote-test',
          messages: [{ role: 'user', content: 'Reply with one short greeting word only.' }],
          max_tokens: 32,
          stream: false,
        }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(typeof payload.detail === 'string' ? payload.detail : payload.error || `HTTP ${resp.status}`);
      }
      const text = payload?.choices?.[0]?.message?.content || payload?.choices?.[0]?.text || '';
      toast(text ? `Remote replied: ${String(text).slice(0, 80)}` : 'Chat test succeeded');
    } catch (err) {
      toast(err.message || 'Chat test failed', false);
    }
  }

  async function removeNode(nodeId) {
    const node = nodes.find((row) => row.id === nodeId);
    const label = node?.label || nodeId;
    if (!window.confirm(`Remove remote node "${label}"?`)) return;
    try {
      await api(`/api/nodes/${encodeURIComponent(nodeId)}`, { method: 'DELETE' });
      toast('Node removed');
      await refreshNodes({ fresh: true });
    } catch (err) {
      toast(err.message || 'Could not remove node', false);
    }
  }

  function hideNodeContextMenu() {
    const menu = document.getElementById('nodesContextMenu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML = '';
    nodeContextTarget = null;
  }

  function positionContextMenu(menu, event) {
    const margin = 8;
    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');
    const rect = menu.getBoundingClientRect();
    let left = event.clientX;
    let top = event.clientY;
    if (left + rect.width + margin > window.innerWidth) {
      left = Math.max(margin, window.innerWidth - rect.width - margin);
    }
    if (top + rect.height + margin > window.innerHeight) {
      top = Math.max(margin, event.clientY - rect.height - margin);
    }
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  async function runNodeContextCommand(cmd, node) {
    if (!node) return;
    if (cmd === 'copy-id') {
      await navigator.clipboard.writeText(node.id || '');
      toast('Node identifier copied');
      return;
    }
    if (cmd === 'copy-url') {
      if (!node.base_url) return;
      await navigator.clipboard.writeText(node.base_url);
      toast('Console URL copied');
      return;
    }
    if (cmd === 'metadata') {
      const modal = document.getElementById('modelMetadataModal');
      const pre = document.getElementById('modelMetadataBody');
      if (pre) pre.textContent = JSON.stringify(node, null, 2);
      modal?.classList.add('open');
      modal?.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      return;
    }
    if (cmd === 'health') {
      await checkNode(node.id);
      return;
    }
    if (cmd === 'chat') {
      await testChat(node.id);
      return;
    }
    if (cmd === 'remove') {
      await removeNode(node.id);
    }
  }

  function openNodeContextMenu(event, node) {
    const menu = document.getElementById('nodesContextMenu');
    if (!menu || !node?.id) return;
    nodeContextTarget = node;
    menu.innerHTML = `
      <button type="button" data-cmd="copy-id">Copy identifier</button>
      <button type="button" data-cmd="copy-url"${node.base_url ? '' : ' disabled'}>Copy Console URL</button>
      <button type="button" data-cmd="metadata">Show metadata</button>
      <hr>
      <button type="button" data-cmd="health">Check health</button>
      <button type="button" data-cmd="chat">Test chat</button>
      <hr>
      <button type="button" data-cmd="remove">Remove node</button>`;
    positionContextMenu(menu, event);
    menu.querySelectorAll('button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', (clickEvent) => {
        clickEvent.stopPropagation();
        void runNodeContextCommand(btn.dataset.cmd, node);
        hideNodeContextMenu();
      });
    });
  }

  function startPolling() {
    stopPolling();
    pollTimer = window.setInterval(() => {
      if (document.body.dataset.activeView !== 'devices') return;
      void refreshNodes({ fresh: true });
    }, 45000);
  }

  function stopPolling() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function bindEvents() {
    document.getElementById('nodesAddBtn')?.addEventListener('click', openAddModal);
    document.getElementById('nodesConnectBtn')?.addEventListener('click', () => void openConnectModal());
    document.getElementById('nodeAddSaveBtn')?.addEventListener('click', () => void saveNode());
    document.getElementById('nodesList')?.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-node-action]');
      if (!btn) return;
      const nodeId = btn.dataset.nodeId || '';
      const action = btn.dataset.nodeAction || '';
      if (action === 'health') void checkNode(nodeId);
      else if (action === 'chat') void testChat(nodeId);
      else if (action === 'remove') void removeNode(nodeId);
    });
    document.getElementById('nodesList')?.addEventListener('contextmenu', (e) => {
      const card = e.target.closest('[data-node-id]');
      if (!card) return;
      const node = nodes.find((row) => row.id === card.dataset.nodeId);
      if (!node) return;
      e.preventDefault();
      openNodeContextMenu(e, node);
    });
    document.addEventListener('click', hideNodeContextMenu);
    document.addEventListener('scroll', hideNodeContextMenu, true);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideNodeContextMenu();
    });
    document.getElementById('nodeConnectBody')?.addEventListener('click', (e) => {
      const pick = e.target.closest('[data-connect-pick]');
      if (pick) {
        connectMethod = pick.dataset.connectPick || '';
        renderConnectWizard();
        return;
      }
      if (e.target.closest('[data-connect-back]')) {
        if (connectMethod) {
          connectMethod = '';
          renderConnectWizard();
        }
        return;
      }
      const scenarioBtn = e.target.closest('[data-connect-scenario]');
      if (scenarioBtn) {
        connectSshScenario = scenarioBtn.dataset.connectScenario || 'reach_remote';
        renderConnectWizard();
      }
    });
    document.getElementById('nodeConnectBody')?.addEventListener('input', (e) => {
      if (connectMethod !== 'ssh') return;
      if (e.target.matches('#nodeConnectSshUser, #nodeConnectSshHost, #nodeConnectLocalPort, #nodeConnectRemotePort')) {
        void refreshSshCommand();
      }
    });
    document.getElementById('nodeConnectFoot')?.addEventListener('click', (e) => {
      if (e.target.closest('[data-connect-back]')) {
        connectMethod = '';
        renderConnectWizard();
        return;
      }
      if (e.target.id === 'nodeConnectTestBtn') void testConnectUrl();
      if (e.target.id === 'nodeConnectAddBtn') void addConnectNode();
    });
    document.getElementById('nodeConnectBody')?.addEventListener('click', (e) => {
      if (e.target.id === 'nodeConnectCopyCmd') {
        copyText(document.getElementById('nodeConnectSshCommand')?.textContent || '');
      }
    });
  }

  async function onViewEnter() {
    await refreshNodes({ fresh: true });
    startPolling();
  }

  function onViewLeave() {
    stopPolling();
  }

  document.addEventListener('DOMContentLoaded', bindEvents);
  window.DFlashNodesLive = { onViewEnter, onViewLeave, refresh: () => refreshNodes({ fresh: true }) };
})();
