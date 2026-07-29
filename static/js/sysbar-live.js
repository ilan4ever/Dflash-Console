/** Live sysbar — CPU, RAM, GPU load + VRAM */
(function () {
  const { api } = window.StudioApi;
  const track = document.getElementById('sysbarTrack');
  let pollTimer = null;

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function pct(value) {
    if (value == null || Number.isNaN(Number(value))) return '—';
    return `${Math.round(Number(value))}%`;
  }

  function gpuCard(gpu) {
    const load = gpu.load_percent ?? 0;
    const vram = gpu.vram_percent ?? 0;
    const tip = gpu.vram_used_gb != null && gpu.vram_total_gb != null
      ? `VRAM ${gpu.vram_used_gb} / ${gpu.vram_total_gb} GB`
      : 'VRAM usage';
    return `
      <div class="lm-sys-card lm-sys-gpu" title="${escapeHtml(tip)}">
        <div class="lm-sys-card-row">
          <span class="lm-sys-card-title">${escapeHtml(gpu.display_name || gpu.name || `GPU ${gpu.index}`)}</span>
          <span class="lm-sys-card-values">
            <span class="lm-sys-val load">${pct(load)}</span>
            <span class="lm-sys-val vram">V ${pct(vram)}</span>
          </span>
        </div>
        <div class="lm-sys-meter"><div class="lm-sys-meter-fill load" style="width:${Math.min(100, load)}%"></div></div>
      </div>`;
  }

  function metricCard(title, value, kind, barWidth, tip) {
    return `
      <div class="lm-sys-card lm-sys-${kind}" title="${escapeHtml(tip || '')}">
        <div class="lm-sys-card-row">
          <span class="lm-sys-card-title">${escapeHtml(title)}</span>
          <span class="lm-sys-card-values">
            <span class="lm-sys-val ${kind}">${pct(value)}</span>
          </span>
        </div>
        <div class="lm-sys-meter"><div class="lm-sys-meter-fill ${kind}" style="width:${Math.min(100, barWidth || 0)}%"></div></div>
      </div>`;
  }

  function render(data) {
    if (!track) return;
    const gpus = Array.isArray(data?.gpus) ? data.gpus : [];
    const ramTip = data.ram_used_gb != null && data.ram_total_gb != null
      ? `${data.ram_used_gb} / ${data.ram_total_gb} GB`
      : 'System RAM';
    const parts = [
      ...gpus.slice(0, 4).map(gpuCard),
      metricCard('CPU', data.cpu_percent, 'cpu', data.cpu_percent, 'CPU utilization'),
      metricCard('RAM', data.ram_percent, 'ram', data.ram_percent, ramTip),
    ];
    track.innerHTML = parts.join('');
  }

  async function refresh() {
    try {
      const data = await api('/api/system-stats');
      render(data);
    } catch {
      if (track && !track.innerHTML) {
        track.innerHTML = metricCard('RAM', null, 'ram', 0, 'Stats unavailable');
      }
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(() => {
      void refresh();
    }, 3000);
  }

  document.addEventListener('DOMContentLoaded', () => {
    void refresh().then(startPolling);
  });

  window.DFlashSysbarLive = { refresh };
})();
