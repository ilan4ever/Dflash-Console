/** DFlash Console — Playground "Speak" panel (Piper TTS). */
(function () {
  const { api } = window.ConsoleApi || {};

  function $(id) {
    return document.getElementById(id);
  }

  let voices = [];
  let sttModels = [];
  let embedServers = [];
  let currentMode = 'chat';
  let speaking = false;
  let transcribing = false;
  let sttLoading = false;
  let embedding = false;
  let lastEmbedResult = null;

  function setStatus(text) {
    const el = $('speakStatus');
    if (el) el.textContent = text || '';
  }

  function setTranscribeStatus(text) {
    const el = $('transcribeStatus');
    if (el) el.textContent = text || '';
  }

  function setMode(mode) {
    currentMode = ['speak', 'transcribe', 'embed'].includes(mode) ? mode : 'chat';
    document.querySelectorAll('[data-playground-mode]').forEach((btn) => {
      const active = btn.dataset.playgroundMode === currentMode;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', String(active));
    });
    const chatBody = document.querySelector('.df-chat-body');
    const composer = document.querySelector('.df-chat-composer');
    const welcome = $('chatWelcome');
    const speakPanel = $('speakPanel');
    const transcribePanel = $('transcribePanel');
    const embedPanel = $('embedPanel');
    const modeIsChat = currentMode === 'chat';
    if (chatBody) chatBody.classList.toggle('hidden', !modeIsChat);
    if (composer) composer.classList.toggle('hidden', !modeIsChat);
    if (welcome) welcome.classList.toggle('hidden', !modeIsChat);
    if (speakPanel) speakPanel.classList.toggle('hidden', currentMode !== 'speak');
    if (transcribePanel) transcribePanel.classList.toggle('hidden', currentMode !== 'transcribe');
    if (embedPanel) embedPanel.classList.toggle('hidden', currentMode !== 'embed');
    if (currentMode === 'speak') {
      void refreshVoices();
      const input = $('speakInput');
      if (input && !input.value) input.focus();
    }
    if (currentMode === 'transcribe') {
      void refreshSttModels();
      setTranscribeStatus('Pick a Whisper model and Load, then choose an audio file and Transcribe.');
    }
    if (currentMode === 'embed') {
      void refreshEmbedServers();
      setEmbedStatus('Pick an embedding server, enter one item per line, and press Embed.');
    }
  }

  function setEmbedStatus(text) {
    const el = $('embedStatus');
    if (el) el.textContent = text || '';
  }

  async function refreshEmbedServers() {
    const pick = $('embedServerPick');
    if (!pick) return;
    if (!embedServers.length) {
      try {
        // /api/servers/profiles always returns normalized all_servers (the
        // status payload may be served from a cache that omits all_servers).
        const data = await api('/api/servers/profiles', { timeoutMs: 10000 });
        const all = Array.isArray(data?.all_servers) ? data.all_servers : [];
        embedServers = all.filter((s) => String(s.engine_mode || '') === 'embedding' || String(s.model_kind || '') === 'embedding');
      } catch (_err) {
        embedServers = [];
      }
    }
    const previous = pick.value;
    pick.innerHTML = '';
    if (!embedServers.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No embedding server found';
      pick.appendChild(opt);
    } else {
      for (const server of embedServers) {
        const opt = document.createElement('option');
        opt.value = server.id || '';
        opt.textContent = server.label || server.id || '';
        pick.appendChild(opt);
      }
      if (previous && embedServers.some((s) => (s.id || '') === previous)) pick.value = previous;
    }
  }

  async function embed() {
    const input = $('embedInput');
    const serverId = $('embedServerPick')?.value || '';
    const items = String(input?.value || '').split(/\n+/).map((t) => t.trim()).filter(Boolean);
    if (!serverId) {
      setEmbedStatus('Pick an embedding server first.');
      return;
    }
    if (!items.length) {
      setEmbedStatus('Enter at least one item to embed.');
      return;
    }
    const btn = $('embedBtn');
    if (embedding) return;
    embedding = true;
    if (btn) btn.disabled = true;
    setEmbedStatus(`Embedding ${items.length} item${items.length === 1 ? '' : 's'}…`);
    try {
      const data = await api(`/api/servers/${encodeURIComponent(serverId)}/embed/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: items.map((text) => ({ text })) }),
        timeoutMs: 120000,
      });
      if (!data?.success) throw new Error(data?.error || 'embedding failed');
      lastEmbedResult = data;
      const dims = data.rows?.[0]?.dimensions || 0;
      const exportBtn = $('embedExportBtn');
      if (exportBtn) exportBtn.hidden = false;
      setEmbedStatus(`${data.count || 0} vectors${dims ? ` · ${dims} dims` : ''}. Ready to export.`);
    } catch (error) {
      setEmbedStatus(error.message || 'Embedding failed.');
    } finally {
      embedding = false;
      if (btn) btn.disabled = false;
    }
  }

  async function embedExport() {
    const serverId = $('embedServerPick')?.value || '';
    const input = $('embedInput');
    const items = String(input?.value || '').split(/\n+/).map((t) => t.trim()).filter(Boolean);
    if (!serverId || !items.length) return;
    const exportBtn = $('embedExportBtn');
    if (exportBtn) exportBtn.disabled = true;
    setEmbedStatus('Exporting .jsonl…');
    try {
      const data = await api(`/api/servers/${encodeURIComponent(serverId)}/embed/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: items.map((text) => ({ text })), export_jsonl: true }),
        timeoutMs: 120000,
      });
      if (!data?.jsonl) throw new Error('no jsonl in response');
      const blob = new Blob([data.jsonl], { type: 'application/x-ndjson' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `embeddings-${Date.now()}.jsonl`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setEmbedStatus(`Exported ${items.length} vectors to .jsonl.`);
    } catch (error) {
      setEmbedStatus(error.message || 'Export failed.');
    } finally {
      if (exportBtn) exportBtn.disabled = false;
    }
  }

  async function refreshSttModels() {
    const pick = $('sttModelPick');
    if (!pick) return;
    if (!sttModels.length) {
      try {
        // Full catalog (not quick) so scanned Whisper GGUF files are included.
        const data = await api('/api/models', { timeoutMs: 20000 });
        sttModels = (Array.isArray(data?.models) ? data.models : []).filter((m) => {
          const name = String(m.filename || m.label || m.path || '').toLowerCase();
          return String(m.modality || '') === 'speech-to-text' || /whisper|faster-whisper/.test(name);
        });
      } catch (_err) {
        sttModels = [];
      }
    }
    const previous = pick.value;
    pick.innerHTML = '';
    if (!sttModels.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No Whisper models found';
      pick.appendChild(opt);
    } else {
      for (const model of sttModels) {
        const opt = document.createElement('option');
        opt.value = model.path || '';
        opt.textContent = model.filename || model.label || model.path || '';
        pick.appendChild(opt);
      }
      if (previous && sttModels.some((m) => (m.path || '') === previous)) pick.value = previous;
    }
  }

  async function loadStt() {
    const pick = $('sttModelPick');
    const path = pick?.value || '';
    if (!path) {
      setTranscribeStatus('Pick a Whisper model to load first.');
      return;
    }
    const btn = $('sttLoadBtn');
    if (sttLoading) return;
    sttLoading = true;
    if (btn) btn.disabled = true;
    setTranscribeStatus('Loading Whisper model… (large models take a few seconds)');
    try {
      const data = await api('/api/runtimes/stt/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
        timeoutMs: 90000,
      });
      if (!data?.success) throw new Error(data?.error || 'load failed');
      setTranscribeStatus(`Whisper model loaded on port ${data.port || '—'}. Choose audio and Transcribe.`);
    } catch (error) {
      setTranscribeStatus(error.message || 'Failed to load the STT model.');
    } finally {
      sttLoading = false;
      if (btn) btn.disabled = false;
    }
  }

  async function refreshVoices() {
    const pick = $('speakVoicePick');
    if (!pick) return;
    try {
      const data = await api('/api/runtimes/piper/voices', { timeoutMs: 8000 });
      voices = Array.isArray(data?.voices) ? data.voices : [];
    } catch (_err) {
      voices = [];
    }
    const previous = pick.value;
    pick.innerHTML = '';
    if (!voices.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No Piper voices found';
      pick.appendChild(opt);
    } else {
      for (const voice of voices) {
        const opt = document.createElement('option');
        opt.value = voice.id || '';
        opt.textContent = voice.label || voice.id || '';
        pick.appendChild(opt);
      }
      if (previous && voices.some((v) => v.id === previous)) pick.value = previous;
    }
    if (currentMode === 'speak') {
      setStatus(
        voices.length
          ? `${voices.length} Piper voice${voices.length === 1 ? '' : 's'} ready. Type text and press Speak.`
          : 'No Piper voices installed. Add .onnx + .onnx.json voices under runtimes/piper/voices/.',
      );
    }
  }

  async function speak() {
    const input = $('speakInput');
    const text = String(input?.value || '').trim();
    if (!text) {
      setStatus('Type some text to speak first.');
      input?.focus();
      return;
    }
    const btn = $('speakBtn');
    const voice = $('speakVoicePick')?.value || '';
    const speed = Number($('speakSpeed')?.value || 1);
    const audio = $('speakAudio');
    const download = $('speakDownload');
    if (speaking) return;
    speaking = true;
    if (btn) btn.disabled = true;
    if (download) download.hidden = true;
    setStatus('Synthesizing…');
    try {
      const response = await fetch('/api/runtimes/piper/v1/audio/speech', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'tts-1',
          input: text,
          voice,
          response_format: 'wav',
          speed,
        }),
      });
      if (!response.ok) {
        let detail = `Synthesis failed (${response.status})`;
        try {
          const body = await response.json();
          if (body?.detail) detail = String(body.detail);
        } catch (_err) { /* keep status detail */ }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      if (audio) {
        audio.src = url;
        audio.hidden = false;
        audio.play().catch(() => {});
      }
      if (download) {
        download.href = url;
        download.download = `speech-${Date.now()}.wav`;
        download.hidden = false;
      }
      const voiceLabel = voices.find((v) => v.id === voice)?.label || voice || 'default';
      setStatus(`Voice ${voiceLabel || 'default'} · ${(blob.size / 1024).toFixed(0)} KB`);
    } catch (error) {
      setStatus(error.message || 'Synthesis failed.');
    } finally {
      speaking = false;
      if (btn) btn.disabled = false;
    }
  }

  async function transcribe() {
    const fileInput = $('transcribeFilePick');
    const file = fileInput?.files?.[0];
    if (!file) {
      setTranscribeStatus('Choose an audio file first.');
      return;
    }
    const btn = $('transcribeBtn');
    if (transcribing) return;
    transcribing = true;
    if (btn) btn.disabled = true;
    setTranscribeStatus('Transcribing…');
    const result = $('transcribeResult');
    if (result) result.value = '';
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('model', 'whisper-1');
      const language = String($('transcribeLanguage')?.value || '').trim();
      if (language) form.append('language', language);
      form.append('response_format', 'json');
      const response = await fetch('/api/runtimes/stt/v1/audio/transcriptions', {
        method: 'POST',
        body: form,
      });
      if (!response.ok) {
        let detail = `Transcription failed (${response.status})`;
        try {
          const body = await response.json();
          if (body?.detail) detail = String(body.detail);
        } catch (_err) { /* keep status detail */ }
        throw new Error(detail);
      }
      const data = await response.json();
      const text = String(data?.text || '');
      if (result) result.value = text;
      setTranscribeStatus(text
        ? `Transcribed ${file.name} (${(file.size / 1024).toFixed(0)} KB).`
        : 'No speech detected in the audio.');
    } catch (error) {
      setTranscribeStatus(error.message || 'Transcription failed.');
    } finally {
      transcribing = false;
      if (btn) btn.disabled = false;
    }
  }

  function bind() {
    document.querySelectorAll('[data-playground-mode]').forEach((btn) => {
      btn.addEventListener('click', () => setMode(btn.dataset.playgroundMode));
    });
    const speakBtn = $('speakBtn');
    if (speakBtn) speakBtn.addEventListener('click', () => void speak());
    const transcribeBtn = $('transcribeBtn');
    if (transcribeBtn) transcribeBtn.addEventListener('click', () => void transcribe());
    const sttLoadBtn = $('sttLoadBtn');
    if (sttLoadBtn) sttLoadBtn.addEventListener('click', () => void loadStt());
    const embedBtn = $('embedBtn');
    if (embedBtn) embedBtn.addEventListener('click', () => void embed());
    const embedExportBtn = $('embedExportBtn');
    if (embedExportBtn) embedExportBtn.addEventListener('click', () => void embedExport());
    const speed = $('speakSpeed');
    if (speed) {
      speed.addEventListener('input', () => {
        const label = $('speakSpeedLabel');
        if (label) label.textContent = `${Number(speed.value).toFixed(1)}×`;
      });
    }
    const input = $('speakInput');
    if (input) {
      input.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
          event.preventDefault();
          void speak();
        }
      });
    }
  }

  function onViewEnter() {
    if (currentMode === 'speak') void refreshVoices();
  }

  window.DFlashSpeakLive = {
    onViewEnter,
    setMode,
    refreshVoices,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
