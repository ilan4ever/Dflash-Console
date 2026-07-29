/** Hardware-aware runtime recommendations for the inspector */
(function () {
  const { api, toast } = window.ConsoleApi;

  let lastPayload = null;
  let refreshTimer = null;
  let applying = false;

  const HINT_IDS = {
    context_size: 'inspectorContextHint',
    max_tokens: 'inspectorMaxTokensHint',
    gpu_layers: 'inspectorGpuHint',
    flash_attention: 'inspectorFlashHint',
    cpu_threads: 'inspectorCpuThreadsHint',
    eval_batch_size: 'inspectorEvalBatchHint',
    physical_batch_size: 'inspectorPhysicalBatchHint',
    temperature: 'inspectorTemperatureHint',
    top_p: 'inspectorTopPHint',
    top_k: 'inspectorTopKHint',
    repeat_penalty: 'inspectorRepeatPenaltyHint',
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function recTag(field) {
    if (!field?.hint) return '';
    const reason = escapeHtml(field.reason || '');
    return `<span class="lm-rec-tag" tabindex="0" aria-label="${reason}" data-tip="${reason}">${escapeHtml(field.hint)}</span>`;
  }

  function setHint(key, field, fallback) {
    const el = document.getElementById(HINT_IDS[key]);
    if (!el) return;
    if (field?.hint) {
      let hint = String(field.hint);
      if (key === 'flash_attention') {
        hint = hint.replace(/^Flash attention\s*/i, '');
      }
      el.innerHTML = recTag({ ...field, hint });
    } else if (fallback) {
      el.textContent = fallback;
    }
  }

  function renderHints(payload, fallbacks) {
    const fields = payload?.fields || {};
    Object.keys(HINT_IDS).forEach((key) => {
      setHint(key, fields[key], fallbacks?.[key]);
    });
    const summary = document.getElementById('inspectorRecSummary');
    if (summary) {
      summary.textContent = payload?.summary || '';
      summary.classList.toggle('hidden', !payload?.summary);
    }
  }

  function buildQuery(model) {
    const params = new URLSearchParams();
    if (model?.server_id) params.set('server_id', model.server_id);
    if (model?.profile) params.set('profile', model.profile);
    if (model?.size_gb != null) params.set('size_gb', String(model.size_gb));
    if (model?.context_max != null) params.set('context_max', String(model.context_max));
    if (model?.gpu_layers_max != null) params.set('gpu_layers_max', String(model.gpu_layers_max));
    return params.toString();
  }

  function fallbackHints(model) {
    const ctxMax = model?.context_max || 262144;
    const gpuMax = model?.gpu_layers_max || 128;
    return {
      context_size: `Model supports up to ${ctxMax} tokens.`,
      gpu_layers: `Layers on GPU (-ngl). Max ${gpuMax}; 99 = all layers.`,
      max_tokens: 'Max output tokens per API request.',
    };
  }

  async function fetchRecommendations(model) {
    if (!model) {
      lastPayload = null;
      renderHints(null, {});
      return null;
    }
    const query = buildQuery(model);
    if (!query) return null;
    try {
      const payload = await api(`/api/runtime-recommendations?${query}`);
      lastPayload = payload;
      renderHints(payload, fallbackHints(model));
      return payload;
    } catch (err) {
      renderHints(null, fallbackHints(model));
      return null;
    }
  }

  function scheduleRefresh(model) {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null;
      void fetchRecommendations(model);
    }, 120);
  }

  function setInputValue(id, value, decimals) {
    const el = document.getElementById(id);
    if (!el || value == null) return;
    if (el.type === 'checkbox') {
      el.checked = !!value;
    } else if (decimals != null) {
      el.value = Number(value).toFixed(decimals);
    } else {
      el.value = String(value);
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  async function applyRecommended() {
    if (!lastPayload?.values || applying) return;
    applying = true;
    try {
      const { context_size: contextSize, load_settings: load, inference_settings: infer } = lastPayload.values;
      setInputValue('inspectorContext', contextSize);
      setInputValue('inspectorGpuLayers', load.gpu_layers);
      setInputValue('inspectorCpuThreads', load.cpu_threads);
      setInputValue('inspectorEvalBatch', load.eval_batch_size);
      setInputValue('inspectorPhysicalBatch', load.physical_batch_size);
      setInputValue('inspectorFlashAttention', load.flash_attention);
      setInputValue('inspectorTemperature', infer.temperature, 2);
      setInputValue('inspectorTopP', infer.top_p, 2);
      setInputValue('inspectorTopK', infer.top_k);
      setInputValue('inspectorRepeatPenalty', infer.repeat_penalty, 2);
      setInputValue('inspectorMaxTokens', infer.max_tokens);
      window.DFlashStatusFeed?.note('Recommended settings applied', lastPayload.summary || 'Based on your hardware');
      toast('Recommended settings applied');
    } finally {
      applying = false;
    }
  }

  function bind() {
    document.getElementById('inspectorApplyRecommended')?.addEventListener('click', () => {
      void applyRecommended();
    });
  }

  document.addEventListener('DOMContentLoaded', bind);

  window.DFlashRuntimeRecommendations = {
    refresh: fetchRecommendations,
    scheduleRefresh,
    applyRecommended,
    getLastPayload: () => lastPayload,
  };
})();
