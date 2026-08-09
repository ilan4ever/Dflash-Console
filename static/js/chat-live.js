/** Playground — load checkpoints and chat against inference engines */
(function () {
  const { toast, api, setSelectLoading } = window.ConsoleApi;
  const STORAGE_KEY = 'dflashConsole.chatSessions';
  const ACTIVE_KEY = 'dflashConsole.chatActiveId';
  const CHECKPOINT_KEY = 'dflashConsole.chatCheckpointKey';

  let sessions = [];
  let activeId = localStorage.getItem(ACTIVE_KEY) || '';
  let selectedCheckpointKey = localStorage.getItem(CHECKPOINT_KEY) || '';
  let selectedSource = '';
  let allServers = [];
  let serverById = new Map();
  let catalogModels = [];
  let sending = false;
  let loadingCheckpoint = false;
  let pollTimer = null;
  let generationPollTimer = null;
  let generationPollInFlight = false;
  let chatRenderFrame = null;
  let pendingAttachments = [];

  const MAX_ATTACHMENTS = 5;
  const MAX_TEXT_FILE_BYTES = 250 * 1024;
  const MAX_IMAGE_FILE_BYTES = 8 * 1024 * 1024;
  const MAX_IMAGE_DATA_URL_CHARS = 2_500_000;
  const MAX_PENDING_ATTACHMENT_CHARS = 3_500_000;
  const MAX_IMAGE_DIMENSION = 1600;
  const IMAGE_CAPABILITY_PATTERN = /vision|multimodal|image|ocr|chandra|ovis|paddleocr|olmocr|[-_]vl[-_]/;
  const TEXT_FILE_PATTERN = /\.(txt|md|markdown|json|csv|tsv|log|py|js|ts|tsx|jsx|html|css|xml|yaml|yml|toml|ini|cfg|sql|sh|ps1)$/i;

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

  function attachmentKind(file) {
    if (String(file?.type || '').toLowerCase().startsWith('image/')) return 'image';
    if (String(file?.type || '').toLowerCase().startsWith('text/') || TEXT_FILE_PATTERN.test(file?.name || '')) {
      return 'text';
    }
    return '';
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener('load', () => resolve(String(reader.result || '')));
      reader.addEventListener('error', () => reject(new Error(`Could not read ${file.name}`)));
      reader.readAsDataURL(file);
    });
  }

  function resizeImageDataUrl(file, dataUrl) {
    if (typeof createImageBitmap !== 'function') return Promise.resolve(dataUrl);
    return createImageBitmap(file).then((bitmap) => {
      const longest = Math.max(bitmap.width, bitmap.height);
      if (longest <= MAX_IMAGE_DIMENSION) {
        bitmap.close?.();
        return dataUrl;
      }
      const scale = MAX_IMAGE_DIMENSION / longest;
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext('2d');
      context?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close?.();
      return canvas.toDataURL('image/jpeg', 0.86);
    }).catch(() => dataUrl);
  }

  async function readAttachment(file) {
    const kind = attachmentKind(file);
    if (!kind) {
      throw new Error(`${file.name} is not a supported image or text file`);
    }
    const maxBytes = kind === 'image' ? MAX_IMAGE_FILE_BYTES : MAX_TEXT_FILE_BYTES;
    if (file.size > maxBytes) {
      const limit = kind === 'image' ? '8 MB' : '250 KB';
      throw new Error(`${file.name} is larger than the ${limit} limit`);
    }
    if (kind === 'text') {
      return {
        id: uid(),
        kind,
        name: file.name,
        type: file.type || 'text/plain',
        size: file.size,
        text: await file.text(),
      };
    }
    const dataUrl = await readFileAsDataUrl(file);
    const normalizedDataUrl = await resizeImageDataUrl(file, dataUrl);
    if (normalizedDataUrl.length > MAX_IMAGE_DATA_URL_CHARS) {
      throw new Error(`${file.name} is too large after image conversion`);
    }
    return {
      id: uid(),
      kind,
      name: file.name,
      type: file.type || 'image/*',
      size: file.size,
      dataUrl: normalizedDataUrl,
    };
  }

  function renderAttachmentTray() {
    const tray = document.getElementById('chatAttachmentTray');
    if (!tray) return;
    tray.classList.toggle('hidden', !pendingAttachments.length);
    tray.innerHTML = pendingAttachments.map((attachment) => {
      const preview = attachment.kind === 'image'
        ? `<img src="${escapeHtml(attachment.dataUrl)}" alt="">`
        : '<span aria-hidden="true">▤</span>';
      return `
        <span class="df-chat-attachment-chip">
          ${preview}
          <span class="df-chat-attachment-name" title="${escapeHtml(attachment.name)}">${escapeHtml(attachment.name)}</span>
          <button type="button" class="df-chat-attachment-remove" data-attachment-id="${escapeHtml(attachment.id)}" aria-label="Remove ${escapeHtml(attachment.name)}">×</button>
        </span>`;
    }).join('');
    tray.querySelectorAll('[data-attachment-id]').forEach((button) => {
      button.addEventListener('click', () => {
        pendingAttachments = pendingAttachments.filter((item) => item.id !== button.dataset.attachmentId);
        renderAttachmentTray();
        updateComposerState();
      });
    });
  }

  function clearPendingAttachments() {
    pendingAttachments = [];
    const picker = document.getElementById('chatFilePick');
    if (picker) picker.value = '';
    renderAttachmentTray();
  }

  async function addAttachments(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;
    const engine = chatReadyEngine();
    const canImages = imageInputSupported(engine);
    const allowed = files.filter((file) => {
      const isImage = /^image\//.test(file.type || '') || /\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name || '');
      if (isImage && !canImages) {
        toast(`${file.name}: this model does not support image input`, false);
        return false;
      }
      return true;
    });
    if (!allowed.length) {
      updateComposerState();
      return;
    }
    const remaining = MAX_ATTACHMENTS - pendingAttachments.length;
    if (allowed.length > remaining) {
      toast(`You can attach up to ${MAX_ATTACHMENTS} files`, false);
    }
    for (const file of allowed.slice(0, Math.max(0, remaining))) {
      try {
        const attachment = await readAttachment(file);
        const nextSize = pendingAttachments.reduce((total, item) => (
          total + (item.kind === 'image' ? item.dataUrl.length : item.text.length)
        ), 0) + (attachment.kind === 'image' ? attachment.dataUrl.length : attachment.text.length);
        if (nextSize > MAX_PENDING_ATTACHMENT_CHARS) {
          throw new Error('The selected files exceed the attachment size limit');
        }
        pendingAttachments.push(attachment);
      } catch (err) {
        toast(err.message || `Could not attach ${file.name}`, false);
      }
    }
    renderAttachmentTray();
    updateComposerState();
  }

  function contentText(content) {
    if (Array.isArray(content)) {
      return content
        .filter((part) => part?.type === 'text')
        .map((part) => String(part.text || ''))
        .join('\n\n');
    }
    return String(content || '');
  }

  function renderUserMessage(msg) {
    const text = contentText(msg.content);
    const attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
    const previews = attachments.map((attachment) => {
      if (attachment.kind === 'image' && attachment.dataUrl) {
        return `<figure class="df-chat-msg-attachment">
          <img src="${escapeHtml(attachment.dataUrl)}" alt="${escapeHtml(attachment.name || 'Attached image')}">
          <figcaption class="df-chat-attachment-name">${escapeHtml(attachment.name || 'Image')}</figcaption>
        </figure>`;
      }
      return `<span class="df-chat-msg-file">▤ ${escapeHtml(attachment.name || 'Attached file')}</span>`;
    }).join('');
    return `${text ? `<div class="df-chat-bubble-text">${escapeHtml(text).replace(/\n/g, '<br>')}</div>` : ''}`
      + (previews ? `<div class="df-chat-msg-attachments">${previews}</div>` : '');
  }

  function imageInputSupported(engine) {
    const model = selectedCatalogModel()
      || catalogModels.find((entry) => entry.server_id === engine?.id || entry.id === engine?.modelId);
    const capabilities = Array.isArray(model?.capabilities) ? model.capabilities : [];
    const haystack = [
      model?.label,
      model?.filename,
      model?.id,
      model?.path,
      model?.pipeline_tag,
      ...capabilities,
    ].join(' ').toLowerCase();
    return capabilities.some((item) => IMAGE_CAPABILITY_PATTERN.test(String(item).toLowerCase()))
      || IMAGE_CAPABILITY_PATTERN.test(haystack);
  }

  function buildMessageContent(text, attachments) {
    const parts = [];
    if (text) parts.push({ type: 'text', text });
    attachments.forEach((attachment) => {
      if (attachment.kind === 'text') {
        parts.push({
          type: 'text',
          text: `[Attached file: ${attachment.name}]\n${attachment.text}`,
        });
      } else if (attachment.kind === 'image') {
        parts.push({ type: 'image_url', image_url: { url: attachment.dataUrl } });
      }
    });
    return parts.length === 1 && parts[0].type === 'text' ? parts[0].text : parts;
  }

  function uid() {
    return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function modelCatalogKey(model) {
    return model?.chat_model_key || model?.server_id || model?.path || model?.id || '';
  }

  function normalizeModelToken(value) {
    return String(value || '').replace(/\\/g, '/').trim().toLowerCase();
  }

  function modelMatchesLoadedServer(model, server) {
    if (!model || !server) return false;
    const loadedTokens = [
      ...(Array.isArray(server.loaded_models) ? server.loaded_models : []),
      server.active_model_id,
    ].map(normalizeModelToken).filter(Boolean);
    const modelTokens = [model.id, model.model_id, model.filename, model.path]
      .map(normalizeModelToken).filter(Boolean);
    if (loadedTokens.some((token) => modelTokens.includes(token))) return true;

    // Older llama-server builds do not expose loaded_models consistently, but
    // the configured profile checkpoint is still unambiguous.
    const configuredTokens = [
      server.model_id,
      server.model_catalog?.api_model_id,
      server.model_catalog?.target_model_id,
      server.model_catalog?.target_path,
      server.target_path,
    ].map(normalizeModelToken).filter(Boolean);
    return server.status === 'loaded'
      && configuredTokens.some((token) => modelTokens.includes(token));
  }

  function modelLoadState(model) {
    const server = serverById.get(String(model?.server_id || ''));
    if (!server) return 'idle';
    if (server.status === 'booting' || server.booting || server.load_progress != null) return 'loading';
    return modelMatchesLoadedServer(model, server) ? 'loaded' : 'idle';
  }

  function isChatModel(model) {
    const modality = String(model?.modality || '').trim().toLowerCase();
    const task = String(model?.task || '').trim().toLowerCase();
    return !['embedding', 'stt', 'tts', 'audio'].includes(modality)
      && !['embed', 'embedding', 'transcribe', 'speech', 'tts'].includes(task);
  }

  // DFlash/DSpark accelerator (draft) checkpoints are small companion files, not
  // standalone chat models — never expose them in the Playground pickers. A real
  // DFlash stack (dflash_stack / draft_path / "dflash" capability) is a chat
  // model and must stay visible.
  function isAcceleratorOnlyModel(model) {
    if (!model) return false;
    const capabilities = Array.isArray(model.capabilities) ? model.capabilities : [];
    const isDflashTarget = !!(
      model.dflash_stack
      || model.draft_path
      || capabilities.includes('dflash')
    );
    if (isDflashTarget) return false;
    const name = `${model.filename || ''} ${model.label || ''}`.toLowerCase();
    return !!name && !name.startsWith('mmproj') && /dflash|dspark/.test(name);
  }

  function appendLoadedChatModels(models) {
    const result = models.filter((model) => (
      !model.chat_model_key
      || modelMatchesLoadedServer(model, serverById.get(String(model.server_id || '')))
    ));
    for (const server of allServers) {
      if (server.engine_mode === 'embedding' || server.model_kind === 'embedding') continue;
      const loadedIds = Array.isArray(server.loaded_models) ? server.loaded_models : [];
      for (const loadedId of loadedIds) {
        const token = normalizeModelToken(loadedId);
        if (!token) continue;
        if (result.some((model) => model.server_id === server.id && modelMatchesLoadedServer(model, server))) continue;
        const rows = [
          ...(Array.isArray(server.visible_cards) ? server.visible_cards : []),
          ...(Array.isArray(server.model_stack) ? server.model_stack : []),
        ];
        const row = rows.find((entry) => [entry?.id, entry?.model_id, entry?.path, entry?.model_path]
          .map(normalizeModelToken).includes(token));
        const path = row?.path || row?.model_path || '';
        const label = row?.title || row?.label || loadedId || server.label || server.id;
        result.push({
          id: loadedId,
          model_id: loadedId,
          chat_model_key: `${server.id}::${loadedId}`,
          server_id: server.id,
          label,
          filename: path.split(/[\\/]/).pop() || loadedId,
          path,
          loadable: !!path,
          source: 'loaded',
          modality: 'llm',
          task: 'chat',
          capabilities: row?.capabilities || [],
          size_gb: row?.size_gb,
        });
      }
    }
    return result;
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
    if (!server || server.status !== 'loaded') return null;
    const selected = selectedCatalogModel();
    if (selected?.server_id === serverId && modelLoadState(selected) !== 'loaded') return null;
    const loadedModels = Array.isArray(server.loaded_models) ? server.loaded_models : [];
    const modelId = String(
      (selected?.server_id === serverId && modelLoadState(selected) === 'loaded'
        ? selected.id || selected.model_id || selected.filename
        : '')
      || server.active_model_id
      || loadedModels[0]
      || server.model_id
      || '',
    ).trim();
    if (!modelId) return null;
    return {
      id: server.id,
      label: server.label || server.id,
      port: server.port,
      modelId,
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
      pinned: false,
    };
  }

  function sessionTitle(session) {
    if (session.title && session.title !== 'New chat') return session.title;
    const first = session.messages.find((m) => m.role === 'user');
    const text = contentText(first?.content).trim().replace(/\s+/g, ' ');
    if (!text) {
      const names = (first?.attachments || []).map((attachment) => attachment.name).filter(Boolean);
      return names.length ? names.join(', ') : 'New chat';
    }
    return text.length > 42 ? `${text.slice(0, 42)}…` : text;
  }

  function selectedCatalogModel() {
    return catalogModels.find((m) => modelCatalogKey(m) === selectedCheckpointKey) || null;
  }

  function syncEngineToModel(model = selectedCatalogModel()) {
    const serverId = String(model?.server_id || '').trim();
    if (!serverId || !serverById.has(serverId)) return '';
    const enginePick = document.getElementById('chatEnginePick');
    if (enginePick) enginePick.value = serverId;
    const session = activeSession();
    if (session) session.serverId = serverId;
    return serverId;
  }

  let catalogRefreshGen = 0;
  let catalogLoaded = false;
  let catalogRetryTimer = null;
  let catalogRetryAttempt = 0;

  function catalogSignature(models) {
    return models.map((m) => modelCatalogKey(m)).join('\n');
  }

  function catalogStateSignature(models) {
    return models.map((model) => `${modelCatalogKey(model)}:${modelLoadState(model)}`).join('\n');
  }

  function sourceOptionsFor(models) {
    if (window.DFlashModelGroups?.sourceOptions) {
      return window.DFlashModelGroups.sourceOptions(models);
    }
    const seen = new Map();
    for (const model of models || []) {
      const id = String(model?.source || model?.provider || model?.library || 'Local').trim() || 'Local';
      if (!seen.has(id)) {
        const label = id.replace(/[-_]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
        seen.set(id, label);
      }
    }
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }

  function scheduleCatalogRetry() {
    if (catalogRetryTimer || catalogRetryAttempt >= 5 || document.body.dataset.activeView !== 'chat') return;
    const delay = Math.min(5000, 750 * (2 ** catalogRetryAttempt));
    catalogRetryAttempt += 1;
    catalogRetryTimer = window.setTimeout(() => {
      catalogRetryTimer = null;
      void refreshCatalog({ force: true });
    }, delay);
  }

  function noteCatalogResult() {
    if (catalogModels.length) {
      catalogRetryAttempt = 0;
      if (catalogRetryTimer) {
        window.clearTimeout(catalogRetryTimer);
        catalogRetryTimer = null;
      }
    } else {
      scheduleCatalogRetry();
    }
  }

  function applyServersData(serversData) {
    allServers = (serversData.servers || []).filter((s) => s.enabled !== false);
    if (!allServers.length) {
      allServers = (serversData.all_servers || []).filter((s) => s.enabled !== false);
    }
    serverById = new Map(allServers.map((s) => [s.id, s]));
  }

  async function refreshCatalog({ force = false } = {}) {
    const gen = ++catalogRefreshGen;
    if (force && catalogRetryTimer) {
      window.clearTimeout(catalogRetryTimer);
      catalogRetryTimer = null;
    }
    const enginePick = document.getElementById('chatEnginePick');
    const modelPick = document.getElementById('chatCheckpointPick');
    const loadBtn = document.getElementById('chatLoadBtn');
    const showLoading = force || !catalogLoaded;

    if (showLoading) {
      setSelectLoading(enginePick, true, 'Loading engines…');
      setSelectLoading(modelPick, true, 'Loading models…');
      if (loadBtn) loadBtn.disabled = true;
      window.DFlashStatusFeed?.setTransient?.('Loading engines and models…', {
        secondary: 'Scanning local model libraries',
        ttlMs: 30000,
      });
    }

    try {
      const [profilesData, quickModelsData] = await Promise.all([
        api('/api/servers/profiles', { timeoutMs: 15000 }),
        api('/api/models?quick=1', { timeoutMs: 15000 }),
      ]);
      if (gen !== catalogRefreshGen) return;
      applyServersData(profilesData);
      catalogModels = (quickModelsData.models || [])
        .filter((m) => (m.path || m.server_id || m.id) && isChatModel(m) && !isAcceleratorOnlyModel(m));
      catalogModels = appendLoadedChatModels(catalogModels);
      catalogLoaded = true;
      noteCatalogResult();
      renderPickers();
      if (showLoading) {
        setSelectLoading(enginePick, false);
        setSelectLoading(modelPick, false);
      }
      updateComposerState();
    } catch {
      if (gen !== catalogRefreshGen) return;
      if (!catalogLoaded) {
        allServers = [];
        serverById = new Map();
        catalogModels = [];
        renderPickers();
        setSelectLoading(enginePick, false);
        setSelectLoading(modelPick, false);
        scheduleCatalogRetry();
      }
    }

    try {
      const [serversData, modelsData] = await Promise.all([
        api('/api/servers?include_external=0', { timeoutMs: 15000 }),
        api('/api/models', { timeoutMs: 30000 }),
      ]);
      if (gen !== catalogRefreshGen) return;
      applyServersData(serversData);
      const nextModels = (modelsData.models || [])
        .filter((m) => (m.path || m.server_id || m.id) && isChatModel(m) && !isAcceleratorOnlyModel(m));
      const mergedModels = appendLoadedChatModels(nextModels);
      const modelsChanged = catalogSignature(mergedModels) !== catalogSignature(catalogModels);
      catalogModels = mergedModels;
      catalogLoaded = true;
      noteCatalogResult();
      if (modelsChanged || showLoading) renderPickers();
      else {
        renderEnginePicker();
        renderCheckpointPicker();
      }
      renderModelTag();
      updateComposerState();
      window.DFlashStatusFeed?.refresh?.();
    } catch {
      if (gen !== catalogRefreshGen) return;
      if (!catalogLoaded) {
        window.DFlashStatusFeed?.note?.('Could not load engines and models', 'Try again in a moment');
        scheduleCatalogRetry();
      }
    }
  }

  async function refreshStatus({ fresh = false } = {}) {
    try {
      const serversData = await api(
        `/api/servers?include_external=0${fresh ? '&fresh=1' : ''}`,
        { timeoutMs: fresh ? 30000 : 15000 },
      );
      applyServersData(serversData);
      catalogModels = appendLoadedChatModels(catalogModels);
      renderEnginePicker();
      renderCheckpointPicker();
      renderModelTag();
      updateComposerState();
    } catch {
      /* keep last known state */
    }
  }

  function renderEnginePicker() {
    const pick = document.getElementById('chatEnginePick');
    if (!pick) return;

    const session = activeSession();
    const prev = pick.value || session?.serverId || '';
    const options = ['<option value="">Select engine…</option>'];
    for (const server of allServers) {
      const status = engineStatusLabel(server);
      const suffix = ` · ${status}`;
      const selected = server.id === prev ? ' selected' : '';
      options.push(
        `<option value="${escapeHtml(server.id)}"${selected}>${escapeHtml(server.label || server.id)}${escapeHtml(suffix)}</option>`,
      );
    }
    const nextHtml = options.join('');
    if (pick.innerHTML !== nextHtml) pick.innerHTML = nextHtml;
    if (prev && allServers.some((s) => s.id === prev)) pick.value = prev;
    else if (allServers[0]) pick.value = allServers[0].id;
  }

  function renderCheckpointPicker() {
    const pick = document.getElementById('chatCheckpointPick');
    if (!pick) return;

    const prev = pick.value || selectedCheckpointKey || '';
    const sourceKey = String(selectedSource || '').trim().toLowerCase();
    const sig = `${catalogSignature(catalogModels)}:${catalogStateSignature(catalogModels)}:${sourceKey}`;
    if (pick.dataset.catalogSig === sig && prev && catalogModels.some((m) => modelCatalogKey(m) === prev)) {
      return;
    }
    pick.dataset.catalogSig = sig;

    const visibleModels = selectedSource
      ? catalogModels.filter((m) => String(window.DFlashModelGroups?.sourceIdFor?.(m) || '').trim().toLowerCase() === sourceKey)
      : catalogModels;
    if (window.DFlashModelGroups?.renderGroupedSelectOptions) {
      pick.innerHTML = window.DFlashModelGroups.renderGroupedSelectOptions(visibleModels, {
        catalogKey: modelCatalogKey,
        optionLabel: (model) => {
          const state = modelLoadState(model);
          const suffix = state === 'loaded' ? ' · Loaded' : state === 'loading' ? ' · Loading…' : '';
          return `${checkpointLabel(model)}${suffix}`;
        },
        placeholder: 'Select model…',
        selectedKey: prev,
      });
    } else {
      const sorted = visibleModels.slice().sort((a, b) => String(a.label || '').localeCompare(String(b.label || '')));
      const options = ['<option value="">Select model…</option>'];
      for (const model of sorted) {
        const key = modelCatalogKey(model);
        const selected = key === prev ? ' selected' : '';
        const state = modelLoadState(model);
        const suffix = state === 'loaded' ? ' · Loaded' : state === 'loading' ? ' · Loading…' : '';
        options.push(
          `<option value="${escapeHtml(key)}"${selected}>${escapeHtml(`${checkpointLabel(model)}${suffix}`)}</option>`,
        );
      }
      pick.innerHTML = options.join('');
    }
    window.DFlashSelectTheme?.syncSelect?.(pick);

    if (prev && catalogModels.some((m) => modelCatalogKey(m) === prev)) {
      pick.value = prev;
      selectedCheckpointKey = prev;
    } else if (selectedCheckpointKey && catalogModels.some((m) => modelCatalogKey(m) === selectedCheckpointKey)) {
      pick.value = selectedCheckpointKey;
    } else if (catalogModels[0] && !prev) {
      selectedCheckpointKey = modelCatalogKey(catalogModels[0]);
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
    renderSourcePicker();
    renderCheckpointPicker();
    renderModelTag();
  }

  function renderSourcePicker() {
    const pick = document.getElementById('chatSourcePick');
    if (!pick) return;
    const options = ['<option value="">All sources</option>'];
    for (const [id, label] of sourceOptionsFor(catalogModels)) {
      options.push(`<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`);
    }
    pick.innerHTML = options.join('');
    pick.value = selectedSource;
    pick.disabled = false;
    pick.classList.remove('is-loading');
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

  function sessionSort(a, b) {
    const pinOrder = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
    if (pinOrder) return pinOrder;
    return (b.createdAt || 0) - (a.createdAt || 0);
  }

  function hideSessionContextMenu() {
    const menu = document.getElementById('chatSessionContextMenu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML = '';
  }

  function commitSessionRename(session, value) {
    const title = String(value || '').trim();
    if (!title) {
      toast('Session name cannot be empty', false);
      return false;
    }
    session.title = title.slice(0, 80);
    persist();
    renderSessionList();
    toast('Session renamed');
    return true;
  }

  function openSessionRenameCard(left, top, session) {
    const menu = document.getElementById('chatSessionContextMenu');
    if (!menu || !session) return;
    menu.innerHTML = `
      <form class="df-chat-session-rename-form">
        <div class="df-chat-session-rename-title">Rename session</div>
        <input class="df-chat-session-rename-input" type="text" maxlength="80" value="${escapeHtml(sessionTitle(session))}" aria-label="Session name">
        <div class="df-chat-session-rename-actions">
          <button type="button" class="lm-btn ghost small" data-cmd="cancel-rename">Cancel</button>
          <button type="submit" class="lm-btn primary small" data-cmd="confirm-rename">Rename</button>
        </div>
      </form>`;
    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');
    const input = menu.querySelector('.df-chat-session-rename-input');
    const boundedLeft = Math.min(left, Math.max(8, window.innerWidth - menu.offsetWidth - 8));
    const boundedTop = Math.min(top, Math.max(8, window.innerHeight - menu.offsetHeight - 8));
    menu.style.left = `${boundedLeft}px`;
    menu.style.top = `${boundedTop}px`;
    input?.focus();
    input?.select();
    const form = menu.querySelector('form');
    form?.addEventListener('click', (clickEvent) => clickEvent.stopPropagation());
    form?.addEventListener('submit', (submitEvent) => {
      submitEvent.preventDefault();
      if (commitSessionRename(session, input?.value)) hideSessionContextMenu();
    });
    menu.querySelector('[data-cmd="cancel-rename"]')?.addEventListener('click', (clickEvent) => {
      clickEvent.stopPropagation();
      hideSessionContextMenu();
    });
    input?.addEventListener('keydown', (keyEvent) => {
      if (keyEvent.key === 'Escape') {
        keyEvent.preventDefault();
        hideSessionContextMenu();
      }
    });
  }

  function openSessionContextMenu(event, session) {
    const menu = document.getElementById('chatSessionContextMenu');
    if (!menu || !session) return;
    menu.innerHTML = `
      <button type="button" data-cmd="rename">Rename session</button>
      <button type="button" data-cmd="pin">${session.pinned ? 'Unpin from top' : 'Pin to top'}</button>
      <hr>
      <button type="button" data-cmd="delete">Delete session</button>`;
    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');
    const left = Math.min(event.clientX, Math.max(8, window.innerWidth - menu.offsetWidth - 8));
    const top = Math.min(event.clientY, Math.max(8, window.innerHeight - menu.offsetHeight - 8));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.querySelectorAll('button[data-cmd]').forEach((button) => {
      button.addEventListener('click', (clickEvent) => {
        clickEvent.stopPropagation();
        if (button.dataset.cmd === 'rename') {
          const rect = menu.getBoundingClientRect();
          openSessionRenameCard(rect.left, rect.top, session);
          return;
        }
        void runSessionContextCommand(button.dataset.cmd, session);
        hideSessionContextMenu();
      });
    });
  }

  async function runSessionContextCommand(command, session) {
    if (!session) return;
    if (command === 'pin') {
      session.pinned = !session.pinned;
      persist();
      renderSessionList();
      toast(session.pinned ? 'Session pinned to top' : 'Session unpinned');
      return;
    }
    if (command === 'delete') {
      if (!window.confirm(`Delete session “${sessionTitle(session)}”?`)) return;
      const wasActive = session.id === activeId;
      sessions = sessions.filter((entry) => entry.id !== session.id);
      if (!sessions.length) {
        const replacement = createSession();
        sessions.push(replacement);
        activeId = replacement.id;
      } else if (wasActive) {
        activeId = sessions.slice().sort(sessionSort)[0]?.id || '';
      }
      persist();
      renderAll();
      toast('Session deleted');
    }
  }

  function renderSessionList() {
    const list = document.getElementById('chatSessionList');
    if (!list) return;
    list.innerHTML = sessions
      .slice()
      .sort(sessionSort)
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
        clearPendingAttachments();
        persist();
        renderAll();
      });
      btn.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const session = sessions.find((entry) => entry.id === btn.dataset.chatId);
        if (session) openSessionContextMenu(event, session);
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
        : renderUserMessage(msg);
      const stats = msg.stats
        ? `<div class="df-chat-msg-stats">${escapeHtml(msg.stats)}</div>`
        : '';
      const pending = msg.pending
        ? '<span class="df-chat-typing"><span class="df-chat-typing-label">Generating</span><span>.</span><span>.</span><span>.</span></span>'
        : '';
      const pendingClass = msg.pending ? ' is-generating' : '';
      return `<article class="df-chat-msg df-chat-msg-${role}${pendingClass}" data-msg-idx="${idx}"${msg.pending ? ' aria-live="polite"' : ''}>
        <div class="df-chat-msg-label">${role === 'user' ? 'You' : 'Assistant'}</div>
        <div class="df-chat-bubble">${body}${pending}${stats}</div>
      </article>`;
    }).join('');

    box.scrollTop = box.scrollHeight;
  }

  function queueChatRender() {
    if (chatRenderFrame != null) return;
    const render = () => {
      chatRenderFrame = null;
      renderMessages();
      updateComposerState();
    };
    if (typeof window.requestAnimationFrame === 'function') {
      chatRenderFrame = window.requestAnimationFrame(render);
    } else {
      chatRenderFrame = window.setTimeout(render, 0);
    }
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
    const attachBtn = document.getElementById('chatAttachBtn');
    const session = activeSession();
    const engine = chatReadyEngine();
    const selectedModel = selectedCatalogModel();
    const selectedState = modelLoadState(selectedModel);
    const ready = !!engine && !sending && !loadingCheckpoint;
    const canLoad = !!document.getElementById('chatEnginePick')?.value
      && !!selectedModel
      && !sending
      && !loadingCheckpoint;

    if (input) {
      input.disabled = !ready;
      if (loadingCheckpoint) input.placeholder = 'Loading model…';
      else if (!ready) input.placeholder = 'Load a model above to start chatting…';
      else if (sending) input.placeholder = 'Waiting for reply…';
      else input.placeholder = 'Message the model… (Enter to send, Shift+Enter for newline)';
    }
    if (sendBtn) sendBtn.disabled = !ready || (!String(input?.value || '').trim() && !pendingAttachments.length);
    if (loadBtn) {
      loadBtn.textContent = selectedState === 'loaded'
        ? 'Use in chat'
        : selectedState === 'loading' || loadingCheckpoint
          ? 'Loading…'
          : 'Load';
      loadBtn.disabled = !canLoad || selectedState === 'loading';
    }
    if (clearBtn) clearBtn.disabled = !session?.messages?.length;
    if (attachBtn) {
      attachBtn.disabled = sending || loadingCheckpoint;
      attachBtn.title = imageInputSupported(chatReadyEngine())
        ? 'Attach images or text files'
        : 'Text files only — this model does not support image input';
    }
    renderModelTag();
  }

  function formatInferenceStats(stats, { live = false } = {}) {
    const source = stats && typeof stats === 'object' ? stats : {};
    const parts = [];
    const generating = live || source.generating === true;
    if (generating) parts.push('Generating');

    const tokens = generating ? source.generating_tokens : source.generation_tokens;
    const tps = generating
      ? source.generating_tokens_per_second
      : source.tokens_per_second;
    if (tokens != null) parts.push(`${Number(tokens) || 0} tok`);
    if (tps != null && Number.isFinite(Number(tps))) {
      parts.push(`${Number(tps).toFixed(1)} t/s`);
    } else if (generating && source.prefill_tokens != null && Number(tokens || 0) <= 0) {
      parts.push(`${Number(source.prefill_tokens) || 0} prefill`);
    }
    return parts.join(' · ');
  }

  function updatePendingAssistantStats(stats) {
    const session = activeSession();
    const pending = [...(session?.messages || [])].reverse().find((msg) => msg?.pending);
    if (!pending) return;
    const label = formatInferenceStats(stats, { live: true }) || 'Generating';
    pending.stats = label;
    if (stats?.generating) setStatus(label);
    queueChatRender();
  }

  async function refreshInferenceStats(serverId, { render = true } = {}) {
    if (!serverId) return null;
    const data = await api(
      `/api/servers/${encodeURIComponent(serverId)}/inference-stats`,
      { timeoutMs: 2500 },
    );
    const stats = data?.inference_stats && typeof data.inference_stats === 'object'
      ? data.inference_stats
      : {};
    const server = serverById.get(serverId);
    if (server) server.inference_stats = stats;
    if (render && sending) updatePendingAssistantStats(stats);
    return stats;
  }

  function stopGenerationPolling() {
    if (generationPollTimer) {
      window.clearInterval(generationPollTimer);
      generationPollTimer = null;
    }
    generationPollInFlight = false;
  }

  function startGenerationPolling(serverId) {
    stopGenerationPolling();
    const poll = async () => {
      if (!sending || generationPollInFlight) return;
      generationPollInFlight = true;
      try {
        await refreshInferenceStats(serverId);
      } catch {
        /* The stream itself remains authoritative if slot polling is unavailable. */
      } finally {
        generationPollInFlight = false;
      }
    };
    void poll();
    generationPollTimer = window.setInterval(poll, 500);
  }

  function renderAll() {
    renderSessionList();
    renderPickers();
    renderAttachmentTray();
    renderMessages();
    updateComposerState();
  }

  function newChat() {
    const session = createSession();
    sessions.unshift(session);
    activeId = session.id;
    clearPendingAttachments();
    persist();
    renderAll();
    document.getElementById('chatInput')?.focus();
  }

  function clearChat() {
    const session = activeSession();
    if (!session) return;
    session.messages = [];
    session.title = 'New chat';
    clearPendingAttachments();
    persist();
    renderAll();
  }

  async function loadCheckpoint() {
    const model = selectedCatalogModel();
    const serverId = syncEngineToModel(model)
      || document.getElementById('chatEnginePick')?.value;
    if (!serverId || !model) {
      toast('Select an engine and model', false);
      return;
    }
    if (!window.DFlashServerLive?.loadModelOnServer) {
      toast('Engine loader is not ready', false);
      return;
    }

    if (modelLoadState(model) === 'loaded') {
      syncSessionEngine();
      setStatus('Model ready — you can chat now');
      updateComposerState();
      document.getElementById('chatInput')?.focus();
      return;
    }

    loadingCheckpoint = true;
    setStatus(`Loading ${model.label || model.id}…`);
    renderAll();
    try {
      const loaded = await window.DFlashServerLive.loadModelOnServer(serverId, model);
      if (!loaded) throw new Error('Model load did not complete. Check the engine log and try again.');
      await refreshStatus({ fresh: true });
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

  function responseText(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      return value
        .map((part) => {
          if (typeof part === 'string') return part;
          return String(part?.text || part?.content || '');
        })
        .join('');
    }
    if (value && typeof value === 'object') {
      return String(value.text || value.content || '');
    }
    return '';
  }

  function streamDeltaText(payload) {
    const choice = payload?.choices?.[0] || {};
    const delta = choice.delta || {};
    return responseText(delta.content ?? delta.text ?? choice.text ?? choice.message?.content);
  }

  function streamMessageText(payload) {
    const choice = payload?.choices?.[0] || {};
    return responseText(choice.message?.content ?? choice.text ?? payload?.content);
  }

  function parseSseBlock(block) {
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
      .trim();
    if (!data || data === '[DONE]') return null;
    try {
      const parsed = JSON.parse(data);
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch {
      return null;
    }
  }

  async function readChatResponse(resp, onDelta) {
    const contentType = String(resp.headers.get('content-type') || '').toLowerCase();
    const isStream = contentType.includes('text/event-stream')
      && resp.body
      && typeof resp.body.getReader === 'function';
    if (!isStream) {
      let payload = null;
      try {
        payload = await resp.json();
      } catch {
        payload = {};
      }
      return {
        payload,
        content: streamMessageText(payload),
        usage: payload?.usage || null,
        timings: payload?.timings || null,
      };
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let content = '';
    let usage = null;
    let timings = null;

    const consume = (block) => {
      const payload = parseSseBlock(block);
      if (!payload) return;
      if (payload.usage && typeof payload.usage === 'object') usage = payload.usage;
      if (payload.timings && typeof payload.timings === 'object') timings = payload.timings;
      const delta = streamDeltaText(payload);
      if (delta) {
        content += delta;
        onDelta?.(content, delta, payload);
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || '';
      blocks.forEach(consume);
    }
    buffer += decoder.decode();
    if (buffer.trim()) consume(buffer);

    return { payload: null, content, usage, timings };
  }

  async function responseError(resp) {
    let payload = null;
    try {
      payload = await resp.json();
    } catch {
      try {
        const text = await resp.text();
        payload = text ? { error: text } : null;
      } catch {
        payload = null;
      }
    }
    const detail = payload?.detail || payload?.error?.message || payload?.error || `HTTP ${resp.status}`;
    return typeof detail === 'string' ? detail : JSON.stringify(detail);
  }

  async function sendMessage() {
    const input = document.getElementById('chatInput');
    const session = activeSession();
    if (!input || !session || sending) return;

    const text = String(input.value || '').trim();
    const attachments = pendingAttachments.slice();
    if (!text && !attachments.length) return;

    const engine = chatReadyEngine();
    if (!engine) {
      toast('Load a model first', false);
      return;
    }
    if (attachments.some((attachment) => attachment.kind === 'image') && !imageInputSupported(engine)) {
      toast('The selected model does not support image or OCR input. Choose a vision/OCR model.', false);
      return;
    }

    session.serverId = engine.id;
    session.modelId = engine.modelId;
    if (session.title === 'New chat') {
      session.title = (text || attachments.map((attachment) => attachment.name).join(', ')).slice(0, 42);
    }

    const messageContent = buildMessageContent(text, attachments);
    const attachmentMeta = attachments.map((attachment) => ({
      kind: attachment.kind,
      name: attachment.name,
      type: attachment.type,
      size: attachment.size,
      ...(attachment.kind === 'image' ? { dataUrl: attachment.dataUrl } : {}),
    }));
    session.messages.push({ role: 'user', content: messageContent, attachments: attachmentMeta });
    const history = session.messages
      .filter((m) => !m.pending && m.content)
      .map((m) => ({ role: m.role, content: m.content }));
    const assistantMessage = { role: 'assistant', content: '', pending: true };
    session.messages.push(assistantMessage);
    input.value = '';
    pendingAttachments = [];
    renderAttachmentTray();
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
      stream: true,
    };

    startGenerationPolling(engine.id);
    try {
      const resp = await fetch(`/api/servers/${encodeURIComponent(engine.id)}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        cache: 'no-store',
      });
      if (!resp.ok) {
        const errText = await responseError(resp);
        throw new Error(errText);
      }

      const result = await readChatResponse(resp, (partialText) => {
        assistantMessage.content = partialText;
        assistantMessage.stats = assistantMessage.stats || 'Generating';
        queueChatRender();
      });
      const choice = result.content || streamMessageText(result.payload) || '';
      const usage = result.usage || result.payload?.usage || {};
      const timings = result.timings || result.payload?.timings || {};
      const gen = usage.completion_tokens;
      const tps = timings.predicted_per_second != null
        ? Number(timings.predicted_per_second).toFixed(1)
        : null;
      const statsParts = [];
      if (gen != null) statsParts.push(`${gen} tok`);
      if (tps) statsParts.push(`${tps} t/s`);
      if (!statsParts.length) {
        const completedStats = formatInferenceStats(
          serverById.get(engine.id)?.inference_stats,
          { live: false },
        ).replace(/^Generating\s*·?\s*/, '');
        if (completedStats) statsParts.push(completedStats);
      }
      assistantMessage.pending = false;
      assistantMessage.content = choice || '(empty response)';
      assistantMessage.stats = statsParts.join(' · ') || undefined;
      setStatus(statsParts.length ? `Last reply · ${statsParts.join(' · ')}` : 'Reply complete');
      void refreshStatus();
      void window.DFlashServerLive?.refresh?.();
    } catch (err) {
      assistantMessage.pending = false;
      assistantMessage.content = `**Error:** ${err.message || 'Request failed'}`;
      assistantMessage.stats = undefined;
      toast(err.message || 'Chat failed', false);
    } finally {
      stopGenerationPolling();
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
      void refreshStatus();
    }, 2500);
  }

  function bind() {
    document.getElementById('chatNewBtn')?.addEventListener('click', newChat);
    document.getElementById('chatClearBtn')?.addEventListener('click', clearChat);
    document.getElementById('chatAttachBtn')?.addEventListener('click', () => {
      document.getElementById('chatFilePick')?.click();
    });
    document.getElementById('chatFilePick')?.addEventListener('change', (event) => {
      void addAttachments(event.target.files);
      event.target.value = '';
    });
    document.getElementById('chatSendBtn')?.addEventListener('click', () => void sendMessage());
    document.getElementById('chatLoadBtn')?.addEventListener('click', () => void loadCheckpoint());

    document.getElementById('chatEnginePick')?.addEventListener('change', () => {
      syncSessionEngine();
      updateComposerState();
    });
    document.getElementById('chatSourcePick')?.addEventListener('change', (e) => {
      selectedSource = e.target.value || '';
      selectedCheckpointKey = '';
      renderCheckpointPicker();
      updateComposerState();
    });

    document.getElementById('chatCheckpointPick')?.addEventListener('change', (e) => {
      selectedCheckpointKey = e.target.value || '';
      syncEngineToModel();
      persist();
      updateComposerState();
    });
    document.addEventListener('click', hideSessionContextMenu);
    document.addEventListener('scroll', hideSessionContextMenu, true);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideSessionContextMenu();
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
    await refreshCatalog({ force: !catalogLoaded });
    syncEngineToModel();
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

  window.DFlashChatLive = { onViewEnter, refreshEngines: () => refreshCatalog({ force: true }) };
})();
