/** Playground — load checkpoints and chat against inference engines */
(function () {
  const { toast, api } = window.ConsoleApi;
  const STORAGE_KEY = 'dflashConsole.chatSessions';
  const ACTIVE_KEY = 'dflashConsole.chatActiveId';
  const CHECKPOINT_KEY = 'dflashConsole.chatCheckpointKey';

  let sessions = [];
  let activeId = localStorage.getItem(ACTIVE_KEY) || '';
  let selectedCheckpointKey = localStorage.getItem(CHECKPOINT_KEY) || '';
  let allServers = [];
  let serverById = new Map();
  let catalogModels = [];
  let sending = false;
  let loadingCheckpoint = false;
  let pollTimer = null;

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderMarkdown(text) {
    if (!text || !window.marked) return escapeHtml(text || '').replace(/\n/g, '<br>');
    const raw = window.marked.parse(String(text), { breaks: true, gfm: true });
    return window.DOMPurify ? window.DOMPurify.sanitize(raw) : raw;
  }

  function uid() {
    return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function modelCatalogKey(model) {
    return model?.server_id || model?.path || model?.id || '';
  }

  function checkpointLabel(model) {
    const parts = [model.label || model.filename || model.id || 'Model'];
    if (model.quant && model.quant !== '—') parts.push(model.quant);
    if (model.size_gb != null) parts.push(`${model.size_gb} GB`);
    if (model.loadable && model.port) parts.push(`:${model.port}`);
    return parts.join(' · ');
  }

  function engineStatusLabel(server) {
    if (!server) return 'unknown';
    if (server.status === 'loaded') return 'loaded';
    if (server.status === 'booting' || server.booting) return 'loading';
    if (server.running || server.status === 'running') return 'idle';
    return 'stopped';
  }

  function chatReadyEngine() {
    const pick = document.getElementById('chatEnginePick');
    const serverId = pick?.value || '';
    const server = serverById.get(serverId);
    if (!server || server.status !== 'loaded' || !server.loaded_models?.length) return null;
    return {
      id: server.id,
      label: server.label || server.id,
      port: server.port,
      modelId: server.loaded_models[0],
      inference: server.inference_settings || {},
    };
  }

  function loadSessions() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      sessions = Array.isArray(parsed) ? parsed : [];
    } catch {
      sessions = [];
    }
    if (!sessions.length) {
      const first = createSession();
      sessions.push(first);
      activeId = first.id;
    }
    if (!sessions.some((s) => s.id === activeId)) {
      activeId = sessions[0]?.id || '';
    }
    persist();
  }

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    if (activeId) localStorage.setItem(ACTIVE_KEY, activeId);
    if (selectedCheckpointKey) localStorage.setItem(CHECKPOINT_KEY, selectedCheckpointKey);
  }

  function activeSession() {
    return sessions.find((s) => s.id === activeId) || null;
  }

  function createSession() {
    const first = allServers[0];
    return {
      id: uid(),
      title: 'New chat',
      serverId: first?.id || '',
      modelId: '',
      messages: [],
      createdAt: Date.now(),
    };
  }

  function sessionTitle(session) {
    if (session.title && session.title !== 'New chat') return session.title;
    const first = session.messages.find((m) => m.role === 'user');
    if (!first?.content) return 'New chat';
    const text = String(first.content).trim().replace(/\s+/g, ' ');
    return text.length > 42 ? `${text.slice(0, 42)}…` : text;
  }

  function selectedCatalogModel() {
    return catalogModels.find((m) => modelCatalogKey(m) === selectedCheckpointKey) || null;
  }

  async function refreshState() {
    try {
      const [serversData, modelsData] = await Promise.all([
        api('/api/servers'),
        api('/api/models'),
      ]);
      allServers = (serversData.servers || []).filter((s) => s.enabled !== false);
      if (!allServers.length) {
        allServers = (serversData.all_servers || []).filter((s) => s.enabled !== false);
      }
      serverById = new Map(allServers.map((s) => [s.id, s]));
      catalogModels = (modelsData.models || []).filter((m) => m.path || m.server_id);
    } catch {
      allServers = [];
      serverById = new Map();
      catalogModels = [];
    }
    renderPickers();
    updateComposerState();
  }

  function renderEnginePicker() {
    const pick = document.getElementById('chatEnginePick');
    if (!pick) return;

    const session = activeSession();
    const prev = session?.serverId || pick.value;
    const options = ['<option value="">Select engine…</option>'];
    for (const server of allServers) {
      const status = engineStatusLabel(server);
      const loaded = server.loaded_models?.[0];
      const suffix = status === 'loaded' && loaded ? ` · ${loaded}` : ` · ${status}`;
      const selected = server.id === prev ? ' selected' : '';
      options.push(
        `<option value="${escapeHtml(server.id)}"${selected}>${escapeHtml(server.label || server.id)}${escapeHtml(suffix)}</option>`,
      );
    }
    pick.innerHTML = options.join('');
    if (prev && allServers.some((s) => s.id === prev)) pick.value = prev;
    else if (allServers[0]) pick.value = allServers[0].id;
  }

  function renderCheckpointPicker() {
    const pick = document.getElementById('chatCheckpointPick');
    if (!pick) return;

    const sorted = catalogModels.slice().sort((a, b) => {
      const aScore = a.loadable ? 0 : 1;
      const bScore = b.loadable ? 0 : 1;
      if (aScore !== bScore) return aScore - bScore;
      return String(a.label || '').localeCompare(String(b.label || ''));
    });

    const options = ['<option value="">Select model…</option>'];
    for (const model of sorted) {
      const key = modelCatalogKey(model);
      const selected = key === selectedCheckpointKey ? ' selected' : '';
      options.push(
        `<option value="${escapeHtml(key)}"${selected}>${escapeHtml(checkpointLabel(model))}</option>`,
      );
    }
    pick.innerHTML = options.join('');
    if (selectedCheckpointKey && sorted.some((m) => modelCatalogKey(m) === selectedCheckpointKey)) {
      pick.value = selectedCheckpointKey;
    } else if (sorted[0]) {
      selectedCheckpointKey = modelCatalogKey(sorted[0]);
      pick.value = selectedCheckpointKey;
      persist();
    }
  }

  function renderModelTag() {
    const tag = document.getElementById('chatModelTag');
    if (!tag) return;
    const ready = chatReadyEngine();
    if (ready) {
      tag.textContent = `Ready · ${ready.modelId}`;
      tag.classList.add('is-ready');
    } else {
      const server = serverById.get(document.getElementById('chatEnginePick')?.value || '');
      if (server && (server.status === 'booting' || server.booting)) {
        tag.textContent = 'Loading…';
      } else {
        tag.textContent = 'Not loaded';
      }
      tag.classList.remove('is-ready');
    }
  }

  function renderPickers() {
    renderEnginePicker();
    renderCheckpointPicker();
    renderModelTag();
  }

  function syncSessionEngine() {
    const session = activeSession();
    const pick = document.getElementById('chatEnginePick');
    if (!session || !pick) return;
    session.serverId = pick.value || '';
    const ready = chatReadyEngine();
    session.modelId = ready?.modelId || '';
    persist();
  }

  function renderSessionList() {
    const list = document.getElementById('chatSessionList');
    if (!list) return;
    list.innerHTML = sessions
      .slice()
      .sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
      .map((session) => {
        const active = session.id === activeId ? ' active' : '';
        const meta = session.messages.length ? `${session.messages.length} msgs` : 'Empty';
        return `<button type="button" class="df-chat-session${active}" data-chat-id="${escapeHtml(session.id)}">
          <span class="df-chat-session-title">${escapeHtml(sessionTitle(session))}</span>
          <span class="df-chat-session-meta">${escapeHtml(meta)}</span>
        </button>`;
      })
      .join('');

    list.querySelectorAll('[data-chat-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        activeId = btn.dataset.chatId || '';
        persist();
        renderAll();
      });
    });
  }

  function renderMessages() {
    const box = document.getElementById('chatMessages');
    const welcome = document.getElementById('chatWelcome');
    const session = activeSession();
    if (!box || !welcome) return;

    const messages = session?.messages || [];
    welcome.classList.toggle('hidden', messages.length > 0);
    box.innerHTML = messages.map((msg, idx) => {
      const role = msg.role === 'user' ? 'user' : 'assistant';
      const body = role === 'assistant'
        ? `<div class="df-chat-bubble-text df-chat-markdown">${renderMarkdown(msg.content || '')}</div>`
        : `<div class="df-chat-bubble-text">${escapeHtml(msg.content || '')}</div>`;
      const stats = msg.stats
        ? `<div class="df-chat-msg-stats">${escapeHtml(msg.stats)}</div>`
        : '';
      const pending = msg.pending ? '<span class="df-chat-typing"><span>.</span><span>.</span><span>.</span></span>' : '';
      return `<article class="df-chat-msg df-chat-msg-${role}" data-msg-idx="${idx}">
        <div class="df-chat-msg-label">${role === 'user' ? 'You' : 'Assistant'}</div>
        <div class="df-chat-bubble">${body}${pending}${stats}</div>
      </article>`;
    }).join('');

    box.scrollTop = box.scrollHeight;
  }

  function setStatus(text) {
    const el = document.getElementById('chatStatus');
    if (el) el.textContent = text || '';
  }

  function updateComposerState() {
    const input = document.getElementById('chatInput');
    const sendBtn = document.getElementById('chatSendBtn');
    const loadBtn = document.getElementById('chatLoadBtn');
    const clearBtn = document.getElementById('chatClearBtn');
    const session = activeSession();
    const engine = chatReadyEngine();
    const ready = !!engine && !sending && !loadingCheckpoint;
    const canLoad = !!document.getElementById('chatEnginePick')?.value
      && !!selectedCatalogModel()
      && !sending
      && !loadingCheckpoint;

    if (input) {
      input.disabled = !ready;
      if (loadingCheckpoint) input.placeholder = 'Loading model…';
      else if (!ready) input.placeholder = 'Load a model above to start chatting…';
      else if (sending) input.placeholder = 'Waiting for reply…';
      else input.placeholder = 'Message the model… (Enter to send, Shift+Enter for newline)';
    }
    if (sendBtn) sendBtn.disabled = !ready || !String(input?.value || '').trim();
    if (loadBtn) loadBtn.disabled = !canLoad;
    if (clearBtn) clearBtn.disabled = !session?.messages?.length;
    renderModelTag();
  }

  function renderAll() {
    renderSessionList();
    renderPickers();
    renderMessages();
    updateComposerState();
  }

  function newChat() {
    const session = createSession();
    sessions.unshift(session);
    activeId = session.id;
    persist();
    renderAll();
    document.getElementById('chatInput')?.focus();
  }

  function clearChat() {
    const session = activeSession();
    if (!session) return;
    session.messages = [];
    session.title = 'New chat';
    persist();
    renderAll();
  }

  async function loadCheckpoint() {
    const serverId = document.getElementById('chatEnginePick')?.value;
    const model = selectedCatalogModel();
    if (!serverId || !model) {
      toast('Select an engine and model', false);
      return;
    }
    if (!window.DFlashServerLive?.loadModelOnServer) {
      toast('Engine loader is not ready', false);
      return;
    }

    loadingCheckpoint = true;
    setStatus(`Loading ${model.label || model.id}…`);
    renderAll();
    try {
      await window.DFlashServerLive.loadModelOnServer(serverId, model);
      await refreshState();
      syncSessionEngine();
      setStatus('Model loaded — you can chat now');
    } catch (err) {
      toast(err.message || 'Load failed', false);
      setStatus('');
    } finally {
      loadingCheckpoint = false;
      renderAll();
    }
  }

  async function sendMessage() {
    const input = document.getElementById('chatInput');
    const session = activeSession();
    if (!input || !session || sending) return;

    const text = String(input.value || '').trim();
    if (!text) return;

    const engine = chatReadyEngine();
    if (!engine) {
      toast('Load a model first', false);
      return;
    }

    session.serverId = engine.id;
    session.modelId = engine.modelId;
    if (session.title === 'New chat') session.title = text.slice(0, 42);

    session.messages.push({ role: 'user', content: text });
    const history = session.messages
      .filter((m) => !m.pending && m.content)
      .map((m) => ({ role: m.role, content: m.content }));
    session.messages.push({ role: 'assistant', content: '', pending: true });
    input.value = '';
    sending = true;
    persist();
    renderAll();
    setStatus('Generating…');

    const body = {
      model: engine.modelId,
      messages: history,
      max_tokens: Number(engine.inference?.max_tokens) || 2048,
      temperature: Number(engine.inference?.temperature ?? 0.7),
      top_p: Number(engine.inference?.top_p ?? 0.9),
    };

    try {
      const resp = await fetch(`/api/servers/${encodeURIComponent(engine.id)}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      let payload = null;
      try {
        payload = await resp.json();
      } catch {
        payload = null;
      }
      session.messages.pop();
      if (!resp.ok) {
        const detail = payload?.detail || payload?.error?.message || payload?.error || `HTTP ${resp.status}`;
        const errText = typeof detail === 'string' ? detail : JSON.stringify(detail);
        session.messages.push({ role: 'assistant', content: `**Error:** ${errText}` });
        toast('Chat request failed', false);
      } else {
        const choice = payload?.choices?.[0]?.message?.content || '';
        const usage = payload?.usage || {};
        const timings = payload?.timings || {};
        const gen = usage.completion_tokens;
        const tps = timings.predicted_per_second != null
          ? Number(timings.predicted_per_second).toFixed(1)
          : null;
        const statsParts = [];
        if (gen != null) statsParts.push(`${gen} tok`);
        if (tps) statsParts.push(`${tps} t/s`);
        session.messages.push({
          role: 'assistant',
          content: choice || '(empty response)',
          stats: statsParts.join(' · ') || undefined,
        });
        setStatus(statsParts.length ? `Last reply · ${statsParts.join(' · ')}` : '');
        void refreshState();
        void window.DFlashServerLive?.refresh?.();
      }
    } catch (err) {
      session.messages.pop();
      session.messages.push({ role: 'assistant', content: `**Error:** ${err.message || 'Request failed'}` });
      toast(err.message || 'Chat failed', false);
    } finally {
      sending = false;
      persist();
      renderAll();
      input.focus();
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      if (document.body.dataset.activeView !== 'chat') return;
      void refreshState();
    }, 2500);
  }

  function bind() {
    document.getElementById('chatNewBtn')?.addEventListener('click', newChat);
    document.getElementById('chatClearBtn')?.addEventListener('click', clearChat);
    document.getElementById('chatSendBtn')?.addEventListener('click', () => void sendMessage());
    document.getElementById('chatLoadBtn')?.addEventListener('click', () => void loadCheckpoint());

    document.getElementById('chatEnginePick')?.addEventListener('change', () => {
      syncSessionEngine();
      updateComposerState();
    });

    document.getElementById('chatCheckpointPick')?.addEventListener('change', (e) => {
      selectedCheckpointKey = e.target.value || '';
      persist();
      updateComposerState();
    });

    const input = document.getElementById('chatInput');
    input?.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
      updateComposerState();
    });
    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!document.getElementById('chatSendBtn')?.disabled) void sendMessage();
      }
    });
  }

  async function onViewEnter() {
    await refreshState();
    syncSessionEngine();
    renderAll();
    startPolling();
    if (chatReadyEngine()) document.getElementById('chatInput')?.focus();
  }

  document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    bind();
    if (document.body.dataset.activeView === 'chat') void onViewEnter();
  });

  window.DFlashChatLive = { onViewEnter, refreshEngines: refreshState };
})();
