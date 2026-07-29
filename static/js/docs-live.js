/** In-app API documentation tab */
(function () {
  const { api } = window.ConsoleApi;

  let catalog = null;
  let activeSection = 'overview';

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function sanitizeHtml(html) {
    if (!html) return '';
    if (!window.DOMPurify) return html;
    return window.DOMPurify.sanitize(html, {
      ADD_ATTR: ['target', 'rel'],
    });
  }

  function renderMarkdown(text) {
    if (!text || !window.marked) return escapeHtml(text || '');
    const raw = window.marked.parse(String(text), { breaks: true, gfm: true });
    return sanitizeHtml(raw);
  }

  function renderEndpoint(ep) {
    const body = ep.body
      ? `<pre class="lm-api-body">${escapeHtml(JSON.stringify(ep.body, null, 2))}</pre>`
      : '';
    const notes = ep.notes ? `<p class="lm-hint">${escapeHtml(ep.notes)}</p>` : '';
    return `
      <div class="lm-api-endpoint">
        <div class="lm-api-endpoint-head">
          <span class="lm-api-method">${escapeHtml(ep.method)}</span>
          <code class="lm-api-path">${escapeHtml(ep.path)}</code>
        </div>
        <p class="lm-api-summary">${escapeHtml(ep.summary || '')}</p>
        ${notes}
        ${body}
      </div>`;
  }

  function renderSection(section) {
    const body = document.getElementById('docsBody');
    if (!body || !section) return;
    body.className = `df-docs-body df-docs-section--${section.id || 'default'}`;
    let html = `<h1 class="df-docs-page-title">${escapeHtml(section.title)}</h1>`;
    if (section.html) {
      html += `<div class="df-docs-rich">${sanitizeHtml(section.html)}</div>`;
    }
    if (section.markdown) {
      html += `<div class="df-docs-markdown">${renderMarkdown(section.markdown)}</div>`;
    }
    if (Array.isArray(section.endpoints) && section.endpoints.length) {
      html += `<div class="lm-api-endpoint-list">${section.endpoints.map(renderEndpoint).join('')}</div>`;
    }
    body.innerHTML = html;
  }

  function renderNav(sections) {
    const nav = document.getElementById('docsNav');
    if (!nav) return;
    nav.innerHTML = sections.map((section) =>
      `<button type="button" class="df-docs-nav-item${section.id === activeSection ? ' active' : ''}" data-docs-section="${escapeHtml(section.id)}">${escapeHtml(section.title)}</button>`,
    ).join('');
    nav.querySelectorAll('[data-docs-section]').forEach((btn) => {
      btn.addEventListener('click', () => {
        activeSection = btn.dataset.docsSection || 'overview';
        renderNav(sections);
        const section = sections.find((s) => s.id === activeSection) || sections[0];
        renderSection(section);
      });
    });
  }

  async function loadCatalog() {
    if (catalog) return catalog;
    catalog = await api('/api/docs/catalog');
    return catalog;
  }

  async function refresh() {
    const data = await loadCatalog();
    const sections = data.sections || [];
    if (!sections.some((s) => s.id === activeSection)) activeSection = sections[0]?.id || 'overview';
    renderNav(sections);
    renderSection(sections.find((s) => s.id === activeSection) || sections[0]);
  }

  async function renderSettingsList() {
    const list = document.getElementById('settingsApiEndpointList');
    if (!list) return;
    const data = await loadCatalog();
    const engineSection = (data.sections || []).find((s) => s.id === 'engines');
    const endpoints = engineSection?.endpoints || [];
    list.innerHTML = endpoints.slice(0, 8).map(renderEndpoint).join('');
  }

  document.addEventListener('DOMContentLoaded', () => {
    void refresh().catch(() => {});
  });

  window.DFlashDocsLive = { refresh, renderSettingsList };
})();
