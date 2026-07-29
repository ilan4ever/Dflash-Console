"""Live inference metrics from llama-server slots and recent completions."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from core.runtime import api_base_url

_LAST_COMPLETION: dict[str, dict[str, Any]] = {}
_ACTIVE_INFERENCE: dict[str, float] = {}


def mark_inference_start(server_id: str) -> None:
    if server_id:
        _ACTIVE_INFERENCE[str(server_id)] = time.time()


def mark_inference_end(server_id: str) -> None:
    if server_id:
        _ACTIVE_INFERENCE.pop(str(server_id), None)


def note_completion_stats(server_id: str, payload: dict[str, Any]) -> None:
    """Cache token counts and speed from an OpenAI-compatible completion response."""
    if not server_id:
        return
    usage = payload.get('usage') if isinstance(payload.get('usage'), dict) else {}
    timings = payload.get('timings') if isinstance(payload.get('timings'), dict) else {}
    entry = {
        'prompt_tokens': usage.get('prompt_tokens'),
        'generation_tokens': usage.get('completion_tokens'),
        'total_tokens': usage.get('total_tokens'),
        'tokens_per_second': None,
        'updated_at': time.time(),
    }
    tps = timings.get('predicted_per_second')
    if tps is not None:
        entry['tokens_per_second'] = round(float(tps), 1)
    else:
        tpt_ms = timings.get('predicted_per_token_ms')
        if tpt_ms and float(tpt_ms) > 0:
            entry['tokens_per_second'] = round(1000.0 / float(tpt_ms), 1)
    _LAST_COMPLETION[str(server_id)] = entry


def _fetch_json(url: str, *, timeout: float = 2.5) -> Any:
    request = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace') or 'null')


def fetch_inference_stats(api_url: str, *, server_id: str = '') -> dict[str, Any]:
    """Return context load and last-request throughput for a running engine."""
    base = api_base_url(api_url)
    stats: dict[str, Any] = {
        'context_tokens': None,
        'tokens_loaded': None,
        'generation_tokens': None,
        'prompt_tokens': None,
        'tokens_per_second': None,
        'updated_at': None,
        'generating': False,
        'generating_seconds': None,
    }
    sid = str(server_id or '')
    active_started = _ACTIVE_INFERENCE.get(sid)
    if active_started is not None:
        stats['generating'] = True
        stats['generating_seconds'] = round(max(0.0, time.time() - active_started), 1)

    if not base:
        last = _LAST_COMPLETION.get(sid) or {}
        for key in ('prompt_tokens', 'generation_tokens', 'tokens_per_second', 'updated_at'):
            if last.get(key) is not None:
                stats[key] = last[key]
        return stats

    try:
        slots_payload = _fetch_json(f'{base}/slots')
        slots = slots_payload if isinstance(slots_payload, list) else (slots_payload or {}).get('slots') or []
        if slots and isinstance(slots[0], dict):
            slot = slots[0]
            stats['context_tokens'] = slot.get('n_ctx')
            stats['tokens_loaded'] = slot.get('n_past') or slot.get('n_prompt_tokens_processed')
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        pass

    try:
        props = _fetch_json(f'{base}/props')
        if isinstance(props, dict) and stats['context_tokens'] is None:
            stats['context_tokens'] = props.get('default_generation_settings', {}).get('n_ctx')
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        pass

    last = _LAST_COMPLETION.get(sid) or {}
    for key in ('prompt_tokens', 'generation_tokens', 'tokens_per_second', 'updated_at'):
        if last.get(key) is not None:
            stats[key] = last[key]
    if stats.get('tokens_per_second') is not None:
        stats['tokens_per_second'] = round(float(stats['tokens_per_second']), 1)
    return stats
