"""Structured API reference served to the UI and settings panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.version import APP_VERSION

_DOCS_ROOT = Path(__file__).resolve().parent.parent / 'docs'


def get_api_catalog(*, console_base: str = 'http://127.0.0.1:8900') -> dict[str, Any]:
    base = console_base.rstrip('/')
    return {
        'success': True,
        'console_base': base,
        'openapi_url': f'{base}/openapi.json',
        'swagger_url': f'{base}/docs',
        'sections': [
            {
                'id': 'overview',
                'title': 'Overview',
                'html': _overview_html(base),
            },
            {
                'id': 'user-guide',
                'title': 'User guide',
                'markdown': _load_user_guide(),
            },
            {
                'id': 'engines',
                'title': 'Engine control',
                'endpoints': _engine_endpoints(),
            },
            {
                'id': 'runtime-json',
                'title': 'Runtime JSON shapes',
                'markdown': _runtime_json_doc(),
            },
            {
                'id': 'engine-openai',
                'title': 'Engine OpenAI API',
                'endpoints': _engine_openai_endpoints(),
            },
            {
                'id': 'console-other',
                'title': 'Console — models, hardware, libraries',
                'endpoints': _other_endpoints(),
            },
        ],
    }


def _load_user_guide() -> str:
    path = _DOCS_ROOT / 'USER-GUIDE.md'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return 'User guide file not found. See `docs/USER-GUIDE.md` in the repository.'
    lines = text.splitlines()
    if lines and lines[0].startswith('# '):
        lines = lines[1:]
    return '\n'.join(lines).strip()


def _overview_html(base: str) -> str:
    return f'''
<div class="df-docs-hero">
  <div class="df-docs-hero-inner">
    <span class="df-docs-hero-badge">DFlash Console · v{APP_VERSION}</span>
    <h2 class="df-docs-hero-title">Local control panel for DFlash engines</h2>
    <p class="df-docs-hero-lead">
      Manage llama-server routers, checkpoint libraries, GPU settings, and live inference stats
      from one LM Studio–style workbench beside your DFlash install.
    </p>
    <div class="df-docs-hero-actions">
      <a class="df-docs-pill primary" href="{base}/" target="_blank" rel="noopener">Open console</a>
      <a class="df-docs-pill" href="{base}/docs" target="_blank" rel="noopener">Swagger UI</a>
      <a class="df-docs-pill" href="https://github.com/ilan4ever/Dflash" target="_blank" rel="noopener">DFlash project</a>
    </div>
  </div>
</div>

<div class="df-docs-feature-grid">
  <article class="df-docs-feature-card">
    <span class="df-docs-feature-icon" aria-hidden="true">⚡</span>
    <h3>Engines</h3>
    <p>Start routers, load checkpoints in parallel, eject without stopping the listener, and watch live token stats on each card.</p>
  </article>
  <article class="df-docs-feature-card">
    <span class="df-docs-feature-icon" aria-hidden="true">▤</span>
    <h3>Checkpoints</h3>
    <p>Scan GGUF and other model folders across library roots. Load loadable profiles straight from the catalog.</p>
  </article>
  <article class="df-docs-feature-card">
    <span class="df-docs-feature-icon" aria-hidden="true">⌕</span>
    <h3>Model catalog</h3>
    <p>Search Hugging Face and download into configured library paths without leaving the app.</p>
  </article>
  <article class="df-docs-feature-card">
    <span class="df-docs-feature-icon" aria-hidden="true">⚙</span>
    <h3>Settings &amp; Locations</h3>
    <p>GPU strategy, engine network, config and preset import/export — all paths in one panel.</p>
  </article>
</div>

<div class="df-docs-strip">
  <div class="df-docs-strip-item">
    <span class="df-docs-strip-label">Console API</span>
    <code>/api/…</code>
    <span class="df-docs-strip-desc">Manage engines, hardware, libraries, and proxied chat</span>
  </div>
  <div class="df-docs-strip-item">
    <span class="df-docs-strip-label">Engine API</span>
    <code>host:port/v1/…</code>
    <span class="df-docs-strip-desc">OpenAI-compatible chat against a running router</span>
  </div>
</div>

<section class="df-docs-section-block">
  <h3>What&apos;s new in v{APP_VERSION}</h3>
  <ul class="df-docs-checklist">
    <li>About page with ILAN AVIV attribution, version, MIT license, and project links</li>
    <li>Live <strong>Generating</strong> timer, token speed, and parallel engine loading</li>
    <li>Model library filters for DFlash stacks, accelerators, downloads, and loaded models</li>
    <li>Hugging Face catalog with README details, install detection, and download progress</li>
    <li>Locations panel with config, preset, and model-library management</li>
    <li>Loopback validation and an external Console data root for the Electron shell</li>
  </ul>
</section>

<section class="df-docs-section-block">
  <h3>Maintainer and public project</h3>
  <p>
    DFlash Console is developed and maintained by <strong>ILAN AVIV</strong> under the MIT License.
    Visit the <a href="https://github.com/ilan4ever/Dflash-Console" target="_blank" rel="noopener">source repository</a>,
    <a href="https://github.com/ilan4ever" target="_blank" rel="noopener">developer profile</a>, or
    <a href="https://github.com/ilan4ever/Dflash" target="_blank" rel="noopener">DFlash project</a>.
  </p>
</section>

<section class="df-docs-section-block">
  <h3>Quick start</h3>
  <ol class="df-docs-steps">
    <li>Copy <code>config.example.json</code> → <code>config.json</code> and set <code>dflash_root</code>.</li>
    <li>Run <code>.\\run.ps1</code> and open <a href="{base}/">{base}/</a>.</li>
    <li>On <strong>Engines</strong>, turn on a profile and load a checkpoint.</li>
    <li>Point your app at the card URL or the console chat proxy.</li>
  </ol>
</section>

<section class="df-docs-section-block muted">
  <h3>API layers</h3>
  <p>
    Send <strong>context window</strong>, <strong>load settings</strong>, and <strong>inference settings</strong>
    as JSON when loading a checkpoint (<code>POST /api/servers/{{id}}/load</code>) or override them per chat request.
    See <strong>Runtime JSON shapes</strong> and <strong>Engine control</strong> in the sidebar.
  </p>
</section>
'''.strip()


def _engine_endpoints() -> list[dict[str, Any]]:
    sid = '{server_id}'
    return [
        {'method': 'GET', 'path': '/api/servers', 'summary': 'List engines with live status and inference stats. Each server includes model_id, status, loaded_models, active_model_id, and ready_for_chat (true only when status is loaded).'},
        {'method': 'GET', 'path': f'/api/servers/{sid}/status', 'summary': 'Status for one engine profile.'},
        {
            'method': 'PATCH',
            'path': f'/api/servers/{sid}',
            'summary': 'Update engine: port, host, context_size, load_settings, inference_settings.',
            'body': {
                'context_size': 65536,
                'load_settings': {'gpu_layers': 99, 'cpu_threads': 8, 'flash_attention': True},
                'inference_settings': {'temperature': 0.7, 'top_p': 0.9, 'max_tokens': 4096},
            },
        },
        {'method': 'POST', 'path': f'/api/servers/{sid}/listen', 'summary': 'Start router only (no checkpoint loaded).'},
        {'method': 'POST', 'path': f'/api/servers/{sid}/engine/start', 'summary': 'Alias for /listen — start engine idle (no model).'},
        {
            'method': 'POST',
            'path': f'/api/servers/{sid}/load',
            'summary': 'Load checkpoint. Optional JSON applies runtime settings before load.',
            'body': {
                'context_size': 32768,
                'load_settings': {'gpu_layers': 99},
                'inference_settings': {'temperature': 0.7, 'max_tokens': 4096},
                'model_path': 'C:\\\\path\\\\to\\\\model.gguf',
                'model_id': 'optional-router-id',
            },
            'notes': 'Omit model_path to load the engine profile default. Pass model_path to load any local GGUF on this engine (LM Studio, library scan, etc.).',
        },
        {'method': 'POST', 'path': f'/api/servers/{sid}/unload', 'summary': 'Unload checkpoint; router stays up.'},
        {'method': 'POST', 'path': f'/api/servers/{sid}/stop', 'summary': 'Stop the engine process.'},
        {'method': 'POST', 'path': f'/api/servers/{sid}/engine/stop', 'summary': 'Alias for /stop — shut down engine process.'},
        {'method': 'POST', 'path': f'/api/servers/{sid}/reload', 'summary': 'Stop and restart with saved settings.'},
        {'method': 'GET', 'path': f'/api/servers/{sid}/inference-stats', 'summary': 'KV token load and last request speed.'},
        {
            'method': 'POST',
            'path': f'/api/servers/{sid}/v1/chat/completions',
            'summary': 'Proxy chat to engine; supports stream:true SSE passthrough. Requires status loaded — POST /load first if running idle.',
            'body': {'model': 'model-id', 'messages': [{'role': 'user', 'content': 'Hello'}], 'max_tokens': 512, 'stream': True},
        },
        {'method': 'GET', 'path': f'/api/logs/{sid}?tail=200', 'summary': 'Tail engine logs.'},
        {'method': 'DELETE', 'path': f'/api/logs/{sid}', 'summary': 'Clear engine log file.'},
    ]


def _engine_openai_endpoints() -> list[dict[str, Any]]:
    return [
        {
            'method': 'POST',
            'path': '{engine_url}/v1/chat/completions',
            'summary': 'Chat completion with per-request sampling; read usage and timings in response.',
            'body': {'model': 'model-id', 'messages': [{'role': 'user', 'content': '…'}], 'max_tokens': 4096},
        },
        {'method': 'GET', 'path': '{engine_url}/v1/models', 'summary': 'List router models.'},
        {'method': 'POST', 'path': '{engine_url}/models/load', 'summary': 'Load model: {"model": "id"}.'},
        {'method': 'POST', 'path': '{engine_url}/models/unload', 'summary': 'Unload active checkpoint.'},
        {'method': 'GET', 'path': '{engine_url}/slots', 'summary': 'Slot state: n_ctx, n_past (context tokens loaded).'},
    ]


def _other_endpoints() -> list[dict[str, Any]]:
    return [
        {'method': 'GET', 'path': '/api/health', 'summary': 'Console liveness.'},
        {'method': 'GET', 'path': '/api/endpoints', 'summary': 'Live list of every console HTTP route (for external apps).'},
        {'method': 'GET', 'path': '/api/installed', 'summary': 'Installed local models grouped by library/provider preset.'},
        {'method': 'GET', 'path': '/api/console/logs?tail=200', 'summary': 'Console, startup, engine, and API-call logs (optional errors_only=1).'},
        {'method': 'GET', 'path': '/api/models', 'summary': 'Checkpoint catalog.'},
        {'method': 'GET', 'path': '/api/hardware', 'summary': 'GPU/CPU and libraries.'},
        {'method': 'PATCH', 'path': '/api/hardware', 'summary': 'GPU strategy and enabled devices.'},
        {'method': 'GET', 'path': '/api/runtime-recommendations', 'summary': 'Hardware-aware runtime suggestions.'},
        {'method': 'GET', 'path': '/api/presets/export', 'summary': 'Export launch preset INI files.'},
        {'method': 'POST', 'path': '/api/presets/import', 'summary': 'Import launch preset INI files.'},
        {'method': 'GET', 'path': '/api/docs/catalog', 'summary': 'Curated API reference JSON for the UI.'},
        {'method': 'GET', 'path': '/openapi.json', 'summary': 'OpenAPI schema with full route definitions.'},
    ]


def _runtime_json_doc() -> str:
    return (
        '**load_settings** — gpu_layers, cpu_threads, eval_batch_size, physical_batch_size, flash_attention\n\n'
        '**inference_settings** — temperature, top_p, top_k, repeat_penalty, max_tokens\n\n'
        '**context_size** — context window in tokens.\n\n'
        'Use in PATCH /api/servers/{id}, POST /api/servers/{id}/load, or override in chat/completions.'
    )
