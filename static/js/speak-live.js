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
  let micRecording = false;
  let micStream = null;
  let micContext = null;
  let micProcessor = null;
  let micChunks = [];
  let runtimePrefs = null;

  function setStatus(text) {
    const el = $('speakStatus');
    if (el) el.textContent = text || '';
  }

  function setTranscribeStatus(text) {
    const el = $('transcribeStatus');
    if (el) el.textContent = text || '';
  }

  function setMicStatus(text) {
    const el = $('micRecordStatus');
    if (el) el.textContent = text || '';
  }

  async function loadRuntimePrefs() {
    if (runtimePrefs) return runtimePrefs;
    const result = { tts: null, stt: null, cpu_slow_warn: true };
    try {
      const [rt, cfg] = await Promise.all([
        api('/api/runtimes', { timeoutMs: 8000 }),
        api('/api/config', { timeoutMs: 8000 }),
      ]);
      const runtimes = (Array.isArray(rt?.runtimes) ? rt.runtimes : []).filter((r) => r.kind === 'runtime');
      result.tts = runtimes.find((r) => r.runtime_id === 'piper') || null;
      result.stt = runtimes.find((r) => r.runtime_id === 'stt') || null;
      result.cpu_slow_warn = (cfg?.config || {}).cpu_slow_warn !== false;
    } catch (_err) { /* keep defaults */ }
    runtimePrefs = result;
    return result;
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
      const prefs = await loadRuntimePrefs();
      const target = previous || prefs.stt?.default_model || '';
      if (target && sttModels.some((m) => (m.path || '') === target)) pick.value = target;
    }
    const prefs = await loadRuntimePrefs();
    const cpuWarn = prefs.cpu_slow_warn && prefs.stt?.device_policy === 'cpu';
    setTranscribeStatus(
      `Pick a Whisper model and Load, then choose an audio file or record from mic, and Transcribe.${cpuWarn ? ' ⚠ Whisper is set to CPU — may be slow.' : ''}`,
    );
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
      const prefs = await loadRuntimePrefs();
      const target = previous || prefs.tts?.default_voice || '';
      if (target && voices.some((v) => v.id === target)) pick.value = target;
    }
    if (currentMode === 'speak') {
      const prefs = await loadRuntimePrefs();
      const cpuWarn = prefs.cpu_slow_warn && prefs.tts?.device_policy === 'cpu';
      setStatus(
        voices.length
          ? `${voices.length} Piper voice${voices.length === 1 ? '' : 's'} ready. Type text and press Speak.${cpuWarn ? ' ⚠ Piper is set to CPU — may be slow.' : ''}`
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

  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeString = (offset, str) => {
      for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    };
    writeString(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, 1, true); // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true); // byte rate
    view.setUint16(32, 2, true); // block align
    view.setUint16(34, 16, true); // bits per sample
    writeString(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }
    return new Blob([buffer], { type: 'audio/wav' });
  }

  async function startMic() {
    if (micRecording) return;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      setMicStatus('Mic unavailable — ' + (err?.message || err?.name || 'denied'));
      return;
    }
    micStream = stream;
    micChunks = [];
    micContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = micContext.createMediaStreamSource(micStream);
    micProcessor = micContext.createScriptProcessor(4096, 1, 1);
    micProcessor.onaudioprocess = (event) => {
      micChunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    source.connect(micProcessor);
    micProcessor.connect(micContext.destination);
    micRecording = true;
    const btn = $('micRecordBtn');
    if (btn) {
      btn.textContent = 'Stop & transcribe';
      btn.classList.add('primary');
    }
    setMicStatus('Recording…');
    setTranscribeStatus('Recording from mic — press “Stop & transcribe” when done.');
  }

  function stopMic() {
    if (!micRecording) return;
    const sampleRate = micContext ? micContext.sampleRate : 16000;
    micRecording = false;
    try { micProcessor?.disconnect(); } catch (_err) { /* noop */ }
    try { micContext?.close(); } catch (_err) { /* noop */ }
    try { micStream?.getTracks().forEach((t) => t.stop()); } catch (_err) { /* noop */ }
    micStream = null;
    micProcessor = null;
    micContext = null;
    const btn = $('micRecordBtn');
    if (btn) {
      btn.textContent = 'Record from mic';
      btn.classList.remove('primary');
    }
    setMicStatus('');
    void transcribeMic(sampleRate);
  }

  async function transcribeMic(sampleRate) {
    if (!micChunks.length) {
      setTranscribeStatus('No audio captured from the mic.');
      return;
    }
    let total = 0;
    for (const chunk of micChunks) total += chunk.length;
    const samples = new Float32Array(total);
    let offset = 0;
    for (const chunk of micChunks) {
      samples.set(chunk, offset);
      offset += chunk.length;
    }
    micChunks = [];
    const blob = encodeWav(samples, sampleRate || 16000);
    if (transcribing) return;
    transcribing = true;
    const btn = $('transcribeBtn');
    if (btn) btn.disabled = true;
    setTranscribeStatus(`Transcribing ${(blob.size / 1024).toFixed(0)} KB mic recording…`);
    const result = $('transcribeResult');
    if (result) result.value = '';
    try {
      const form = new FormData();
      form.append('file', blob, 'mic.wav');
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
      setTranscribeStatus(text ? 'Mic transcription complete.' : 'No speech detected in the mic audio.');
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
    const micBtn = $('micRecordBtn');
    if (micBtn) {
      micBtn.addEventListener('click', () => {
        if (micRecording) stopMic();
        else void startMic();
      });
    }
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
