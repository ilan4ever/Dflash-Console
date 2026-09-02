"""Structured API reference served to the UI and settings panel."""

from __future__ import annotations

from typing import Any

from core.config import PACKAGE_ROOT
from core.version import APP_VERSION

_DOCS_ROOT = PACKAGE_ROOT / 'docs'


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
                'id': 'cli',
                'title': 'Terminal CLI',
                'markdown': _load_cli_guide(),
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
                'id': 'multi-modal',
                'title': 'Multi-modal runtimes (TTS · STT · Embed)',
                'markdown': _multimodal_guide_md(),
                'endpoints': _multimodal_endpoints(),
            },
            {
                'id': 'gateway',
                'title': 'Console OpenAI gateway (port 8001)',
                'markdown': _gateway_guide_md(),
                'endpoints': _gateway_endpoints(),
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


def _load_markdown_doc(name: str) -> str:
    path = _DOCS_ROOT / name
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return f'Documentation file not found. See `docs/{name}` in the repository.'
    lines = text.splitlines()
    if lines and lines[0].startswith('# '):
        lines = lines[1:]
    return '\n'.join(lines).strip()


def _load_user_guide() -> str:
    return _load_markdown_doc('USER-GUIDE.md')


def _load_cli_guide() -> str:
    return _load_markdown_doc('CLI.md')


def _overview_html(base: str) -> str:
    return f'''
<div class="df-docs-hero">
  <div class="df-docs-hero-inner">
    <span class="df-docs-hero-badge">DFlash Console · v{APP_VERSION}</span>
    <h2 class="df-docs-hero-title">Local control panel for DFlash, vLLM, Transformers, and FreeToken</h2>
    <p class="df-docs-hero-lead">
      Load GGUF and Hugging Face models, attach DFlash 1 or DFlash 2 drafts, and talk to
      every engine from one loopback workbench.
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
    <p>DFlash / llama-server, vLLM, Transformers, and FreeToken. Load in parallel and watch live token stats on each card.</p>
  </article>
  <article class="df-docs-feature-card">
    <span class="df-docs-feature-icon" aria-hidden="true">▤</span>
    <h3>DFlash stacks</h3>
    <p>DFlash 1 and DFlash 2 drafts. Right-click a target and find a compatible accelerator automatically.</p>
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
    <li><strong>Four LLM engines</strong> — DFlash / llama-server, vLLM, Transformers, and FreeToken</li>
    <li><strong>DFlash 1 and DFlash 2</strong> — architecture-aware draft search, validation, and attach</li>
    <li><strong>Public preview</strong> — Windows installer on <a href="https://github.com/ilan4ever/Dflash-Console/releases/latest" target="_blank" rel="noopener">GitHub Releases</a> and <strong>pip install dflash-console</strong></li>
    <li>Installed apps update from the latest GitHub Release (<code>latest.json</code> + setup EXE)</li>
    <li><strong>dflash serve</strong> keeps one Console on port 8900 — it stops a foreign instance first</li>
    <li>Playground <strong>Chat · Speak · Transcribe · Embed</strong> plus the OpenAI gateway on port 8001</li>
  </ul>
</section>

<section class="df-docs-section-block">
  <h3>Maintainer and public project</h3>
  <p>
    DFlash Console is developed and maintained by <strong>ILAN AVIV</strong> under
    the GNU Affero General Public License version 3 or later.
    Visit the <a href="https://github.com/ilan4ever/Dflash-Console" target="_blank" rel="noopener">source repository</a>,
    <a href="https://github.com/ilan4ever" target="_blank" rel="noopener">developer profile</a>, or
    <a href="https://github.com/ilan4ever/Dflash" target="_blank" rel="noopener">DFlash project</a>.
    The AGPL requires retaining the copyright and license notices and providing
    corresponding source when its distribution or network-use terms apply.
    There is no warranty for the covered work. See the repository license and
    trademark policy for the complete terms.
  </p>
</section>

<section class="df-docs-section-block">
  <h3>Install</h3>
  <ul class="df-docs-checklist">
    <li><strong>Windows (recommended):</strong> download <code>DFlash-Console-Setup-*-x64.exe</code> from <a href="https://github.com/ilan4ever/Dflash-Console/releases/latest" target="_blank" rel="noopener">GitHub Releases</a></li>
    <li><strong>Terminal CLI:</strong> <code>pip install dflash-console</code> then <code>dflash serve</code> (PyPI package name; command is <code>dflash</code>)</li>
    <li><strong>Git checkout:</strong> copy <code>config.example.json</code> → <code>config.json</code>, set <code>dflash_root</code>, then <code>.\\run.ps1</code> or <code>dflash serve</code></li>
  </ul>
  <p>Only one Console API should listen on port 8900. The desktop app and <code>dflash serve</code> stop a foreign instance before starting.</p>
</section>

<section class="df-docs-section-block">
  <h3>Quick start</h3>
  <ol class="df-docs-steps">
    <li>Install with the Windows setup EXE, <code>pip install dflash-console</code>, or a git checkout.</li>
    <li>Start the server (<code>dflash serve</code>, <code>.\\run.ps1</code>, or the desktop app) and open <a href="{base}/">{base}/</a>.</li>
    <li>On <strong>Engines</strong>, pick DFlash, vLLM, Transformers, or FreeToken, then load a model.</li>
    <li>For a DFlash GGUF, right-click <strong>Find and attach draft</strong> if you want speculative decoding.</li>
    <li>Point your app at <code>http://127.0.0.1:8001/v1</code> or the console chat proxy.</li>
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
        {'method': 'GET', 'path': '/api/models', 'summary': 'Full local model library (same list as the Models tab). Optional source=ollama|lmstudio|dflash|library, quick=1 for engine profiles only, refresh=1 to rescan.'},
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


def _multimodal_guide_md() -> str:
    return (
        '**Load any model with one call** — `POST /api/models/load` with `{"path": "<catalog path>"}`. '
        'The console looks the model up in the catalog, detects its modality, and dispatches to the '
        'right runtime automatically (whisper for speech-to-text, piper for text-to-speech, vLLM, '
        'Transformers, or FreeToken for SafeTensors LLMs, llama-server for GGUF llm/embedding/vision/ocr '
        'and DFlash 1 / DFlash 2 stacks — pass `runtime_id` or `server_id` to choose the engine).\n\n'
        'Every row of `GET /api/models` now carries a `load_route` field with the exact call to use '
        '(method + path + body).\n\n'
        '### By model type\n'
        '- **LLM / chat** — `POST /api/models/load {"path": "…", "runtime_id": "vllm|transformers|freetoken"}` '
        '(GGUF uses llama-server / DFlash). Then chat on `/api/servers/{id}/v1/chat/completions`.\n'
        '- **DFlash draft** — `POST /api/stacks/find-and-attach-draft {"path": "<target.gguf>"}` finds a compatible '
        'DFlash 1 or DFlash 2 accelerator, registers it, and attaches it.\n'
        '- **Text to speech (Piper)** — `POST /api/models/load {"path": "<voice .onnx>"}` then '
        '`POST /api/runtimes/piper/v1/audio/speech {"input": "hello", "voice": "en_US-lessac-medium"}` → WAV bytes.\n'
        '- **Speech to text (Whisper)** — `POST /api/models/load {"path": "<whisper .gguf>"}` then '
        '`POST /api/runtimes/stt/v1/audio/transcriptions` (multipart `file=<audio>`).\n'
        '- **Embeddings** — load an embedding engine (`POST /api/models/load` or the nomic profile), then '
        '`POST /api/servers/{id}/v1/embeddings {"input": ["text", …]}` or `POST /api/servers/{id}/embed/batch` for JSONL export.\n'
        '- **Vision / OCR** — `GET /api/models/vision/plan?path=…` to plan mmproj wiring, then chat with an '
        'image via `/api/servers/{id}/v1/chat/completions` on the vision engine.\n\n'
        'Runtimes run as native bundles under `runtimes/` with a per-runtime `manifest.json`; device policy, '
        'CPU fallback, VRAM budget, default voice/model, and loading-behavior toggles are configured in '
        '**Settings → Speech & runtimes** (`config.json` → `runtimes[]`).'
    )


def _multimodal_endpoints() -> list[dict[str, Any]]:
    rid = '{runtime_id}'
    sid = '{server_id}'
    return [
        {'method': 'GET', 'path': '/api/components', 'summary': 'Install hub: vLLM, Transformers, FreeToken, speech runtimes, update reminders, active HF downloads.'},
        {'method': 'GET', 'path': '/api/runtimes', 'summary': 'List runtimes + adapters (piper, stt, vllm, transformers, freetoken) and every runtime_id, modality, execution mode.'},
        {'method': 'GET', 'path': '/api/runtimes/manifests', 'summary': 'Aggregated bundle manifests + process identity tokens.'},
        {'method': 'GET', 'path': f'/api/runtimes/{rid}', 'summary': 'Runtime health: running, port, active model.'},
        {'method': 'GET', 'path': f'/api/runtimes/{rid}/voices', 'summary': 'Available Piper voices (id + label).'},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/start', 'summary': 'Start a server-mode runtime (whisper). CLI runtimes (piper) report started:false (always ready).'},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/stop', 'summary': 'Stop a server-mode runtime process.'},
        {'method': 'GET', 'path': f'/api/runtimes/{rid}/install', 'summary': 'On-demand install status for vLLM, Transformers, or FreeToken.'},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/install', 'summary': 'Start an on-demand vLLM, Transformers, or FreeToken download.', 'body': {'backend': 'auto', 'torch_variant': 'auto'}},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/uninstall', 'summary': 'Remove an on-demand vLLM, Transformers, or FreeToken install.'},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/load', 'summary': 'Load a model into the runtime (whisper .gguf, piper voice, vLLM/Transformers/FreeToken folder).', 'body': {'path': 'C:\\\\models\\\\whisper\\\\model_q4_k.gguf'}},
        {'method': 'POST', 'path': '/api/stacks/find-draft', 'summary': 'Search local libraries and Hugging Face for a compatible DFlash 1 or DFlash 2 draft.', 'body': {'path': 'C:\\\\models\\\\target.gguf'}},
        {'method': 'POST', 'path': '/api/stacks/find-and-attach-draft', 'summary': 'Find, register, and attach a compatible DFlash draft to the target model.', 'body': {'path': 'C:\\\\models\\\\target.gguf'}},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/unload', 'summary': 'Unload the active model and free GPU memory.'},
        {'method': 'POST', 'path': f'/api/models/load', 'summary': 'Unified loader — load ANY catalog model by path; dispatches by modality.', 'body': {'path': 'C:\\\\models\\\\model.gguf', 'server_id': 'optional-llama-engine'}},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/v1/audio/speech', 'summary': 'OpenAI-style text-to-speech → WAV (Piper).', 'body': {'input': 'Hello', 'voice': 'en_US-lessac-medium', 'speed': 1.0}},
        {'method': 'POST', 'path': f'/api/runtimes/{rid}/v1/audio/transcriptions', 'summary': 'OpenAI-style speech-to-text (Whisper); multipart file=<audio>.', 'body': {'file': '<audio file>', 'model': 'whisper-1', 'language': 'en'}},
        {'method': 'POST', 'path': f'/api/servers/{sid}/v1/embeddings', 'summary': 'Embed text on an embedding engine.', 'body': {'input': ['one', 'two'], 'model': 'nomic-embed'}},
        {'method': 'POST', 'path': f'/api/servers/{sid}/embed/batch', 'summary': 'Batch embed + export .jsonl.', 'body': {'input': ['one item per line', '…'], 'model': 'nomic-embed'}},
        {'method': 'GET', 'path': '/api/hf/search', 'summary': 'Hugging Face model search (same data as the Model catalog UI).'},
        {'method': 'POST', 'path': '/api/hf/download', 'summary': 'Download a GGUF/safetensors file into a configured library.'},
        {'method': 'GET', 'path': '/api/hf/downloads', 'summary': 'List active Hugging Face downloads and finished download history.'},
        {'method': 'DELETE', 'path': '/api/hf/downloads/{job_id}', 'summary': 'Remove one finished download from history. Active jobs are left alone.'},
        {'method': 'DELETE', 'path': '/api/hf/downloads', 'summary': 'Clear finished download history. Active downloads are left alone.'},
        {'method': 'POST', 'path': '/api/hf/install', 'summary': 'Search Hugging Face, download GGUF shard(s), and optionally load the model.', 'body': {'query': 'qwen dflash', 'load': True, 'server_id': 'optional-engine-id'}},
        {'method': 'GET', 'path': '/api/status/loaded', 'summary': 'Currently loaded models across engines and non-llama runtimes.'},
        {'method': 'GET', 'path': '/api/status/report', 'summary': 'Full machine report: CPU/RAM/VRAM, engines, runtimes, loaded models.'},
        {'method': 'GET', 'path': '/api/system-stats', 'summary': 'Live CPU, RAM, and GPU utilization for the sysbar.'},
        {'method': 'GET', 'path': '/api/runtimes/{runtime_id}/logs', 'summary': 'Tail the per-runtime log file.'},
    ]


def _gateway_guide_md() -> str:
    return (
        '**One OpenAI-compatible port for everything.** The Console OpenAI gateway listens on '
        '`gateway_port` (**default 8001**) and proxies chat, embeddings, TTS and STT to whichever '
        'engine is loaded — so any OpenAI-compatible app (OpenAI SDKs, LangChain, Cursor, VS Code, '
        'SillyTavern, …) can point at one stable base URL::\n\n'
        '```\nbase_url = http://127.0.0.1:8001/v1\napi_key  = anything (ignored)\n```\n\n'
        '**Model names are tolerant (LM-Studio style).** Send the engine id (`gemma-12b-ar`), the '
        'real checkpoint id (`gemma-4-12b-it-qat`), or any alias (e.g. `gpt-4o`) — the gateway '
        'resolves it to the configured chat engine and rewrites the request for you. `GET /v1/models` '
        'lists every enabled engine so clients can discover them.\n\n'
        'The default chat engine is `config.json -> gateway_server_id` (falls back to the first '
        'enabled non-embedding engine). Set it in **Settings → Engine profiles → Console OpenAI gateway**, '
        'or in the API via `GET/PATCH /api/config` (`gateway_port`, `gateway_server_id`).\n\n'
        'Chat auto-loads the model on first use (JIT) and streams with Server-Sent Events when '
        '`"stream": true`; the engine\'s VRAM guard still protects against over-commit. The gateway '
        'starts and stops together with the Console (check `GET /api/gateway` or `GET http://127.0.0.1:{port}/health`).'
    )


def _gateway_endpoints() -> list[dict[str, Any]]:
    return [
        {'method': 'GET', 'path': '/api/gateway', 'summary': 'Console-side status of the gateway: port, url, running flag, default server id, routes.'},
        {'method': 'GET', 'path': '/health', 'summary': 'Gateway health (checks the Console is reachable).'},
        {'method': 'GET', 'path': '/', 'summary': 'Gateway info banner with the /v1 base URL.'},
        {'method': 'GET', 'path': '/v1/models', 'summary': 'List every enabled engine as an OpenAI model (id = engine id; meta carries engine, embedding, model_id).'},
        {'method': 'POST', 'path': '/v1/chat/completions', 'summary': 'Chat on the default engine; any model name accepted; streaming when stream=true; JIT-loads the model.', 'body': {'model': 'any-name', 'messages': [{'role': 'user', 'content': 'Hello'}]}},
        {'method': 'POST', 'path': '/v1/embeddings', 'summary': 'Embed text on the default embedding engine.', 'body': {'input': ['one', 'two'], 'model': 'nomic-embed'}},
        {'method': 'POST', 'path': '/v1/audio/speech', 'summary': 'OpenAI-style text-to-speech → WAV (Piper).', 'body': {'input': 'Hello', 'voice': 'en_US-lessac-medium', 'speed': 1.0}},
        {'method': 'POST', 'path': '/v1/audio/transcriptions', 'summary': 'OpenAI-style speech-to-text (Whisper); multipart file=<audio>.', 'body': {'file': '<audio file>', 'model': 'whisper-1'}},
    ]
