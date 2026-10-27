"""External model servers the Console does not manage but can still serve.

Some apps run a model outside the Console and expose an OpenAI-compatible API.
Strata is one example: its engine holds the graphics card while Strata's own
server answers on 127.0.0.1.  A Strata engine started through a registered
Console launcher is a Console-owned card instead; the scan skips those cards
and ``_launcher_entries`` keeps the model reachable through the gateway.

The list is built from the external GPU scan, which already measures each
workload's VRAM and finds its listening port.  A short cache keeps a chat
request from running nvidia-smi more than once every few seconds.
"""

from __future__ import annotations

import re
import time
from typing import Any

# Ids other apps already use for their own model names must never be captured
# by an external entry: these four are advertised by the gateway for Cursor.
_RESERVED_IDS = {'gpt-4', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini'}

_CACHE_TTL_S = 30.0
# A resolved id -> upstream keeps working for longer: a chat request must never
# wait for a GPU scan, and a wrong port is corrected on the next scan.
_RESOLVED_TTL_S = 300.0
_CACHE: dict[str, Any] = {'at': 0.0, 'entries': []}
_RESOLVED: dict[str, tuple[float, dict[str, Any]]] = {}

# Generic process names say nothing about which app runs the model, and a
# llama-server started by another tool is not something to advertise as a
# standalone model.  Named apps (Strata, LM Studio, Ollama, ...) are.
_GENERIC_APPS = {'', 'llama-server', 'python', 'pythonw', 'unknown', 'other'}


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')


def _api_base(api_url: str) -> str:
    """The server root (http://host:port), without a trailing /v1."""
    text = str(api_url or '').strip().rstrip('/')
    return re.sub(r'/v\d+$', '', text)


def _chat_url(api_url: str) -> str:
    """The OpenAI chat route for an upstream that may name only its root."""
    base = _api_base(api_url)
    return f'{base}/v1/chat/completions' if base else ''


def _upstream_context_size(api_base: str) -> int | None:
    """The context the upstream reports in /health, when it answers quickly."""
    if not api_base:
        return None
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f'{api_base}/health', timeout=0.6) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace'))
        value = int(data.get('max_context') or 0)
        return value if value >= 2048 else None
    except Exception:
        return None


def _launcher_entries(entries: list[dict[str, Any]], seen: set[str]) -> None:
    """Add models the Console started itself (external launchers).

    A launcher-managed engine (Strata) is not always attributed by the GPU
    process scan, but the Console still owns it and its gateway must offer it.
    """
    try:
        from core.external_launchers import _http_json as _launcher_http_json
        from core.external_launchers import launcher_model_id, launchers
    except Exception:
        return
    for launcher in launchers():
        if not isinstance(launcher, dict):
            continue
        health_url = str(launcher.get('health_url') or '').strip()
        port = launcher.get('port')
        if not health_url or not port:
            continue
        health = _launcher_http_json(health_url, timeout=1.5)
        if not isinstance(health, dict):
            health = {}
        api_model = str(health.get('model') or '').strip() or launcher_model_id(launcher)
        if not api_model:
            continue
        app = _slug(launcher.get('id') or '')
        label = str(launcher.get('label') or api_model).strip() or api_model
        context_size = health.get('max_context')
        candidates: list[str] = []
        if app:
            candidates.append(f'{app}-{_slug(api_model)}')
        candidates.append(api_model.lower())
        for public_id in candidates:
            if not public_id or public_id in seen or public_id in _RESERVED_IDS:
                continue
            seen.add(public_id)
            entries.append({
                'id': public_id,
                'label': label,
                'api_url': f'http://127.0.0.1:{int(port)}/v1',
                'api_model_id': api_model,
                'app_source': app,
                'pid': None,
                'context_size': context_size,
                'loaded': bool(health.get('loaded')),
                'launcher_id': str(launcher.get('id') or '').strip(),
                'external': True,
            })


def _scan_entries() -> list[dict[str, Any]]:
    try:
        from core.gpu_processes import get_external_gpu_loads

        cards = get_external_gpu_loads()
    except Exception:
        cards = []

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        if card.get('console_engine') is True:
            # A launcher-managed engine (Strata) is Console-owned. Its public
            # model ids come from _launcher_entries, not the external scan.
            continue
        api_url = str(card.get('api_url') or '').strip().rstrip('/')
        api_model = str(card.get('model_id') or '').strip()
        if not api_url or not api_model:
            continue
        app = _slug(card.get('app_source') or '')
        if app in _GENERIC_APPS:
            continue
        # A file path in the model field is a llama-server started with -m, not
        # a server that names its model; keep it out of the public list.
        if '\\' in api_model or '/' in api_model or api_model.lower().endswith('.gguf'):
            continue
        chat_url = _chat_url(api_url)
        if not chat_url:
            continue
        openai_base = f'{_api_base(api_url)}/v1'
        label = str(card.get('title') or card.get('model_name') or api_model).strip() or api_model
        context_size = card.get('context_size') or _upstream_context_size(_api_base(api_url))
        candidates: list[str] = []
        if app:
            candidates.append(f'{app}-{_slug(api_model)}')
        candidates.append(api_model.lower())
        label_slug = _slug(label)
        if label_slug:
            candidates.append(label_slug)
        for public_id in candidates:
            if not public_id or public_id in seen or public_id in _RESERVED_IDS:
                continue
            seen.add(public_id)
            entries.append({
                'id': public_id,
                'label': label,
                'api_url': openai_base,
                'api_model_id': api_model,
                'app_source': app,
                'pid': card.get('pid'),
                'context_size': context_size,
                'external': True,
            })
    _launcher_entries(entries, seen)
    return entries


def external_model_entries(*, force: bool = False) -> list[dict[str, Any]]:
    """External workloads that answer the OpenAI API, with the ids the gateway lists."""
    now = time.monotonic()
    if not force and _CACHE['entries'] and now - float(_CACHE['at'] or 0.0) < _CACHE_TTL_S:
        return [dict(row) for row in _CACHE['entries']]
    entries = _scan_entries()
    _CACHE['at'] = now
    _CACHE['entries'] = entries
    return [dict(row) for row in entries]


def resolve_external_model_entry(model: str) -> dict[str, Any] | None:
    """The external workload a client asked for by name, if any."""
    wanted = str(model or '').strip().lower()
    if not wanted or wanted in _RESERVED_IDS:
        return None
    cached = _RESOLVED.get(wanted)
    if cached and time.monotonic() - cached[0] < _RESOLVED_TTL_S:
        return dict(cached[1])
    for entry in external_model_entries():
        if str(entry.get('id') or '').lower() == wanted:
            _RESOLVED[wanted] = (time.monotonic(), dict(entry))
            return entry
    _RESOLVED.pop(wanted, None)
    return None


def forget_external_models() -> None:
    """Drop the cached list (after a load or unload changes the cards)."""
    _CACHE['at'] = 0.0
    _CACHE['entries'] = []
    _RESOLVED.clear()
