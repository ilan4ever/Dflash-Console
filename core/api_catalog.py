"""Structured API reference served to the UI and settings panel."""

from __future__ import annotations

from typing import Any


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
                'markdown': (
                    'DFlash Console exposes two layers of API:\n\n'
                    '1. **Console API** (`/api/...`) — manage engines, checkpoints, hardware, and load/unload with full runtime JSON.\n'
                    '2. **Engine API** (`http://host:port/v1/...`) — OpenAI-compatible chat against a running llama-server router.\n\n'
                    'Send **context window**, **load settings**, and **inference settings** as JSON when loading a checkpoint or in each chat request.'
                ),
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


def _engine_endpoints() -> list[dict[str, Any]]:
    sid = '{server_id}'
    return [
        {'method': 'GET', 'path': '/api/servers', 'summary': 'List engines with live status and inference stats.'},
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
            },
        },
        {'method': 'POST', 'path': f'/api/servers/{sid}/unload', 'summary': 'Unload checkpoint; router stays up.'},
        {'method': 'POST', 'path': f'/api/servers/{sid}/stop', 'summary': 'Stop the engine process.'},
        {'method': 'POST', 'path': f'/api/servers/{sid}/engine/stop', 'summary': 'Alias for /stop — shut down engine process.'},
        {'method': 'POST', 'path': f'/api/servers/{sid}/reload', 'summary': 'Stop and restart with saved settings.'},
        {'method': 'GET', 'path': f'/api/servers/{sid}/inference-stats', 'summary': 'KV token load and last request speed.'},
        {
            'method': 'POST',
            'path': f'/api/servers/{sid}/v1/chat/completions',
            'summary': 'Proxy chat to engine; updates inference-stats from response timings.',
            'body': {'model': 'model-id', 'messages': [{'role': 'user', 'content': 'Hello'}], 'max_tokens': 512},
        },
        {'method': 'GET', 'path': f'/api/logs/{sid}?tail=200', 'summary': 'Tail engine logs.'},
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
        {'method': 'GET', 'path': '/api/models', 'summary': 'Checkpoint catalog.'},
        {'method': 'GET', 'path': '/api/hardware', 'summary': 'GPU/CPU and libraries.'},
        {'method': 'PATCH', 'path': '/api/hardware', 'summary': 'GPU strategy and enabled devices.'},
        {'method': 'GET', 'path': '/api/runtime-recommendations', 'summary': 'Hardware-aware runtime suggestions.'},
        {'method': 'GET', 'path': '/api/docs/catalog', 'summary': 'Full API reference JSON.'},
    ]


def _runtime_json_doc() -> str:
    return (
        '**load_settings** — gpu_layers, cpu_threads, eval_batch_size, physical_batch_size, flash_attention\n\n'
        '**inference_settings** — temperature, top_p, top_k, repeat_penalty, max_tokens\n\n'
        '**context_size** — context window in tokens.\n\n'
        'Use in PATCH /api/servers/{id}, POST /api/servers/{id}/load, or override in chat/completions.'
    )
