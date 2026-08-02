"""Live inference metrics from llama-server slots and recent completions."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

from core.runtime import api_base_url

_LAST_COMPLETION: dict[str, dict[str, Any]] = {}
_LAST_COMPLETION_SLOTS: dict[str, dict[int, dict[str, Any]]] = {}
# Reference-counted so parallel map→merge chat proxies stay "generating"
# until the last in-flight request ends (avoids status thrashing mid-batch).
_ACTIVE_INFERENCE: dict[str, dict[str, Any]] = {}
_ACTIVE_INFERENCE_LOCK = threading.Lock()
_LIVE_TRACK: dict[str, dict[str, Any]] = {}
_LIVE_TRACK_SLOTS: dict[str, dict[int, dict[str, Any]]] = {}
_SLOT_PREV_GENERATING: dict[str, dict[int, bool]] = {}
_PREV_ANY_GENERATING: dict[str, bool] = {}
_WAVE_EPOCH: dict[str, int] = {}
_SLOT_WAVE_EPOCH: dict[str, dict[int, int]] = {}
_WAVE_TASK_SNAPSHOT: dict[str, dict[int, int]] = {}
_STATS_CACHE: dict[str, dict[str, Any]] = {}
_LIVE_CACHE_MAX_AGE = 1.0


def _proxy_inference_started_at(server_id: str) -> float | None:
    sid = str(server_id or '')
    if not sid:
        return None
    with _ACTIVE_INFERENCE_LOCK:
        row = _ACTIVE_INFERENCE.get(sid)
    if not isinstance(row, dict):
        return None
    try:
        return float(row.get('started_at'))
    except (TypeError, ValueError):
        return None


def _clear_last_completions(server_id: str) -> None:
    sid = str(server_id or '')
    if not sid:
        return
    _LAST_COMPLETION.pop(sid, None)
    _LAST_COMPLETION_SLOTS.pop(sid, None)
    _SLOT_WAVE_EPOCH.pop(sid, None)


def _snapshot_wave_tasks(server_id: str, raw_slots: list[dict[str, Any]]) -> None:
    sid = str(server_id or '')
    if not sid:
        return
    _WAVE_TASK_SNAPSHOT[sid] = {
        _slot_id(slot): int(slot.get('id_task') or 0)
        for slot in raw_slots
    }


def _slot_task_advanced(server_id: str, slot: dict[str, Any]) -> bool:
    sid = str(server_id or '')
    slot_id = _slot_id(slot)
    snap = _WAVE_TASK_SNAPSHOT.get(sid) or {}
    prev_task = int(snap.get(slot_id, 0) or 0)
    curr_task = int(slot.get('id_task') or 0)
    return curr_task > prev_task


def _current_wave_epoch(server_id: str) -> int:
    return int(_WAVE_EPOCH.get(str(server_id or ''), 0))


def _note_generating_wave(
    server_id: str,
    *,
    any_generating: bool,
    raw_slots: list[dict[str, Any]] | None = None,
) -> None:
    """Clear stale Last stats when a new inference wave starts after idle."""
    sid = str(server_id or '')
    if not sid:
        return
    prev = _PREV_ANY_GENERATING.get(sid, False)
    if any_generating and not prev:
        _clear_last_completions(sid)
        _WAVE_EPOCH[sid] = _current_wave_epoch(sid) + 1
        _snapshot_wave_tasks(sid, raw_slots or [])
        active = {
            _slot_id(slot)
            for slot in (raw_slots or [])
            if _slot_generation_state(slot)[0]
        }
        prev_map = _SLOT_PREV_GENERATING.setdefault(sid, {})
        for slot_id in list(prev_map.keys()):
            if slot_id not in active:
                prev_map[slot_id] = False
    if not any_generating:
        _snapshot_wave_tasks(sid, raw_slots or [])
    _PREV_ANY_GENERATING[sid] = any_generating


def _mark_slot_wave_completion(server_id: str, slot_id: int) -> None:
    sid = str(server_id or '')
    if sid:
        _SLOT_WAVE_EPOCH.setdefault(sid, {})[int(slot_id)] = _current_wave_epoch(sid)


def _clear_live_stats(stats: dict[str, Any]) -> None:
    stats['generating'] = False
    stats['generating_seconds'] = None
    stats['generating_tokens'] = None
    stats['generating_tokens_per_second'] = None
    stats.pop('live_updated_at', None)


def _slot_id(slot: dict[str, Any]) -> int:
    try:
        return int(slot.get('id', 0))
    except (TypeError, ValueError):
        return 0


def _completion_entry(
    *,
    generation_tokens: int,
    prompt_tokens: int | None = None,
    tokens_per_second: float | None = None,
) -> dict[str, Any]:
    total = None
    if prompt_tokens is not None:
        total = int(prompt_tokens) + int(generation_tokens)
    return {
        'prompt_tokens': prompt_tokens,
        'generation_tokens': int(generation_tokens),
        'total_tokens': total,
        'tokens_per_second': round(float(tokens_per_second), 1) if tokens_per_second else None,
        'updated_at': time.time(),
    }


def get_cached_inference_stats(server_id: str) -> dict[str, Any]:
    cached = _STATS_CACHE.get(str(server_id or ''))
    if not isinstance(cached, dict):
        return {}
    stats = dict(cached)
    if not stats.get('generating'):
        return stats
    live_at = float(stats.get('live_updated_at') or 0.0)
    if live_at and (time.time() - live_at) > _LIVE_CACHE_MAX_AGE and not is_proxy_generating(server_id):
        _clear_live_stats(stats)
    return stats


def mark_inference_start(
    server_id: str,
    *,
    api_url: str = '',
    model_id: str = '',
) -> None:
    sid = str(server_id or '')
    if not sid:
        return
    with _ACTIVE_INFERENCE_LOCK:
        row = _ACTIVE_INFERENCE.get(sid)
        if isinstance(row, dict) and int(row.get('count') or 0) > 0:
            row['count'] = int(row.get('count') or 0) + 1
            starting_fresh = False
        else:
            _ACTIVE_INFERENCE[sid] = {'count': 1, 'started_at': time.time()}
            starting_fresh = True
    if starting_fresh:
        _clear_last_completions(sid)
        _SLOT_PREV_GENERATING.pop(sid, None)
        _WAVE_EPOCH[sid] = _current_wave_epoch(sid) + 1
        _PREV_ANY_GENERATING[sid] = True
        try:
            base = api_base_url(api_url)
            if base:
                slots_url = f'{base}/slots'
                model = str(model_id or '').strip()
                if model:
                    slots_url = f'{slots_url}?{urlencode({"model": model})}'
                raw = _fetch_json(slots_url)
                rows = raw if isinstance(raw, list) else (raw or {}).get('slots') or []
                _snapshot_wave_tasks(sid, [row for row in rows if isinstance(row, dict)])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
            pass


def mark_inference_end(server_id: str) -> None:
    sid = str(server_id or '')
    if not sid:
        return
    cleared = False
    with _ACTIVE_INFERENCE_LOCK:
        row = _ACTIVE_INFERENCE.get(sid)
        if not isinstance(row, dict):
            _ACTIVE_INFERENCE.pop(sid, None)
            cleared = True
        else:
            row['count'] = max(0, int(row.get('count') or 0) - 1)
            if row['count'] <= 0:
                _ACTIVE_INFERENCE.pop(sid, None)
                cleared = True
    if cleared:
        cached = _STATS_CACHE.get(sid)
        if isinstance(cached, dict):
            _clear_live_stats(cached)
            _STATS_CACHE[sid] = cached


def note_completion_stats(
    server_id: str,
    payload: dict[str, Any],
    *,
    api_url: str = '',
    model_id: str = '',
) -> None:
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
    sid = str(server_id)
    _LAST_COMPLETION[sid] = entry
    slot_id = _match_completion_slot(
        sid,
        api_url=api_url,
        model_id=model_id,
        generation_tokens=entry.get('generation_tokens'),
    )
    if slot_id is not None:
        _LAST_COMPLETION_SLOTS.setdefault(sid, {})[int(slot_id)] = dict(entry)
        _mark_slot_wave_completion(sid, int(slot_id))


def note_completion_from_live(
    server_id: str,
    *,
    generation_tokens: int | None,
    prompt_tokens: int | None = None,
    tokens_per_second: float | None = None,
    slot_id: int | None = None,
) -> None:
    """Persist Last stats after live slot monitoring sees a finished decode."""
    if not server_id or generation_tokens is None or int(generation_tokens) <= 0:
        return
    sid = str(server_id)
    if slot_id is not None:
        last_by_slot = _LAST_COMPLETION_SLOTS.setdefault(sid, {})
        prev = last_by_slot.get(int(slot_id)) or {}
        if prev.get('generation_tokens') == int(generation_tokens):
            return
        entry = _completion_entry(
            generation_tokens=int(generation_tokens),
            prompt_tokens=prompt_tokens,
            tokens_per_second=tokens_per_second,
        )
        last_by_slot[int(slot_id)] = entry
        _LAST_COMPLETION[sid] = entry
        _mark_slot_wave_completion(sid, int(slot_id))
        return
    last = _LAST_COMPLETION.get(sid) or {}
    if last.get('generation_tokens') == int(generation_tokens):
        return
    _LAST_COMPLETION[sid] = _completion_entry(
        generation_tokens=int(generation_tokens),
        prompt_tokens=prompt_tokens,
        tokens_per_second=tokens_per_second,
    )
    if slot_id is None:
        _mark_slot_wave_completion(sid, 0)


def _slot_decoded_count(slot: dict[str, Any]) -> int | None:
    _, n_decoded = _slot_generation_state(slot)
    return int(n_decoded) if n_decoded is not None else None


def _match_completion_slot(
    server_id: str,
    *,
    api_url: str,
    model_id: str,
    generation_tokens: Any,
) -> int | None:
    if generation_tokens is None:
        return None
    try:
        target = int(generation_tokens)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    base = api_base_url(api_url)
    if not base:
        return None
    try:
        slots_url = f'{base}/slots'
        model = str(model_id or '').strip()
        if model:
            slots_url = f'{slots_url}?{urlencode({"model": model})}'
        raw = _fetch_json(slots_url)
        rows = raw if isinstance(raw, list) else (raw or {}).get('slots') or []
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            decoded = _slot_decoded_count(row)
            if decoded == target:
                candidates.append(row)
        if not candidates:
            return None
        if len(candidates) == 1:
            return _slot_id(candidates[0])
        advanced = [row for row in candidates if _slot_task_advanced(server_id, row)]
        pool = advanced or candidates
        pool.sort(key=lambda row: int(row.get('id_task') or 0), reverse=True)
        return _slot_id(pool[0])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return None


def _fetch_json(url: str, *, timeout: float = 0.9) -> Any:
    request = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8', errors='replace') or 'null')


def _slot_generation_state(slot: dict[str, Any]) -> tuple[bool, int | None]:
    next_tokens = slot.get('next_token')
    info: dict[str, Any] = {}
    if isinstance(next_tokens, dict):
        info = next_tokens
    elif isinstance(next_tokens, list) and next_tokens and isinstance(next_tokens[0], dict):
        info = next_tokens[0]
    n_decoded = info.get('n_decoded')
    if n_decoded is not None:
        n_decoded = int(n_decoded)
    generating = bool(slot.get('is_processing'))
    return generating, n_decoded


def _begin_live_track(server_id: str, *, at: float | None = None) -> dict[str, Any]:
    sid = str(server_id or '')
    now = float(at or time.time())
    track = _LIVE_TRACK.get(sid)
    if not track or not track.get('started_at'):
        track = {'started_at': now, 'tokens': None, 'at': now, 'tps': None}
        _LIVE_TRACK[sid] = track
    return track


def _end_live_track(server_id: str) -> None:
    _LIVE_TRACK.pop(str(server_id or ''), None)


def _decode_tokens_per_second(track: dict[str, Any] | None, tokens: int | None) -> float | None:
    """Decode-only throughput — excludes long prompt prefill from the denominator."""
    if not track or tokens is None:
        return None
    decoded = int(tokens)
    if decoded <= 0:
        return None
    decode_started = track.get('decode_started_at')
    if decode_started is None:
        return None
    elapsed = max(0.001, time.time() - float(decode_started))
    if elapsed < 0.15:
        return None
    base = int(track.get('decode_base_tokens') or 0)
    produced = max(0, decoded - base)
    if produced <= 0:
        return None
    return round(produced / elapsed, 1)


def _live_tokens_per_second(track: dict[str, Any] | None, tokens: int | None) -> float | None:
    return _decode_tokens_per_second(track, tokens)


def is_proxy_generating(server_id: str) -> bool:
    sid = str(server_id or '')
    if not sid:
        return False
    with _ACTIVE_INFERENCE_LOCK:
        row = _ACTIVE_INFERENCE.get(sid)
    return isinstance(row, dict) and int(row.get('count') or 0) > 0


def _slot_has_activity(slot_stats: dict[str, Any], *, server_id: str = '') -> bool:
    if slot_stats.get('generating'):
        return True
    sid = str(server_id or '')
    slot_id = int(slot_stats.get('slot_id') or 0)
    slot_epoch = (_SLOT_WAVE_EPOCH.get(sid) or {}).get(slot_id)
    if slot_epoch != _current_wave_epoch(sid):
        return False
    return slot_stats.get('generation_tokens') is not None or slot_stats.get('prompt_tokens') is not None


def _apply_slot_last(out: dict[str, Any], last: dict[str, Any]) -> None:
    for key in ('prompt_tokens', 'generation_tokens', 'tokens_per_second', 'updated_at'):
        if last.get(key) is not None:
            out[key] = last[key]


def _process_slot(server_id: str, slot: dict[str, Any]) -> dict[str, Any]:
    sid = str(server_id)
    slot_id = _slot_id(slot)
    generating, n_decoded = _slot_generation_state(slot)
    prompt_raw = slot.get('n_prompt_tokens')
    slot_prompt = int(prompt_raw) if prompt_raw is not None else None

    last_by_slot = _LAST_COMPLETION_SLOTS.setdefault(sid, {})
    track_by_slot = _LIVE_TRACK_SLOTS.setdefault(sid, {})
    prev_by_slot = _SLOT_PREV_GENERATING.setdefault(sid, {})
    was_generating = prev_by_slot.get(slot_id, False)
    prev_by_slot[slot_id] = generating

    out: dict[str, Any] = {
        'slot_id': slot_id,
        'generating': generating,
        'generating_seconds': None,
        'generating_tokens': None,
        'generating_tokens_per_second': None,
        'prompt_tokens': None,
        'generation_tokens': None,
        'tokens_per_second': None,
    }

    if generating:
        track = track_by_slot.get(slot_id)
        if not track or not track.get('started_at'):
            track = {
                'started_at': time.time(),
                'tokens': None,
                'tps': None,
                'decode_started_at': None,
                'decode_base_tokens': 0,
            }
            track_by_slot[slot_id] = track
        out['generating_seconds'] = round(max(0.0, time.time() - float(track['started_at'])), 1)
        prefill_raw = slot.get('n_prompt_tokens_processed')
        if prefill_raw is not None:
            try:
                out['prefill_tokens'] = int(prefill_raw)
            except (TypeError, ValueError):
                pass
        decoded = int(n_decoded or 0)
        out['generating_tokens'] = decoded
        if decoded > 0:
            if track.get('decode_started_at') is None:
                track['decode_started_at'] = time.time()
                track['decode_base_tokens'] = 0
            track['tokens'] = decoded
            live_tps = _live_tokens_per_second(track, decoded)
            if live_tps is not None:
                out['generating_tokens_per_second'] = live_tps
                track['tps'] = live_tps
        _apply_slot_last(out, last_by_slot.get(slot_id) or {})
        return out

    track = track_by_slot.pop(slot_id, None)
    task_advanced = _slot_task_advanced(sid, slot)
    if (was_generating or task_advanced) and n_decoded is not None and int(n_decoded) > 0:
        api_entry = _LAST_COMPLETION.get(sid) or {}
        slot_entry = (_LAST_COMPLETION_SLOTS.get(sid) or {}).get(slot_id) or {}
        fresh_api = (
            api_entry.get('generation_tokens') == int(n_decoded)
            and api_entry.get('tokens_per_second') is not None
            and (time.time() - float(api_entry.get('updated_at') or 0)) < 45.0
        )
        fresh_slot = (
            slot_entry.get('generation_tokens') == int(n_decoded)
            and slot_entry.get('tokens_per_second') is not None
            and (time.time() - float(slot_entry.get('updated_at') or 0)) < 45.0
        )
        if fresh_slot:
            entry = dict(slot_entry)
        elif fresh_api:
            entry = dict(api_entry)
        else:
            decode_tps = _decode_tokens_per_second(track, int(n_decoded)) if track else None
            entry = _completion_entry(
                generation_tokens=int(n_decoded),
                prompt_tokens=slot_prompt,
                tokens_per_second=decode_tps or ((track or {}).get('tps')),
            )
        last_by_slot[slot_id] = entry
        _LAST_COMPLETION[sid] = entry
        _mark_slot_wave_completion(sid, slot_id)
    slot_epoch = (_SLOT_WAVE_EPOCH.get(sid) or {}).get(slot_id)
    if slot_epoch == _current_wave_epoch(sid):
        _apply_slot_last(out, last_by_slot.get(slot_id) or {})
    return out


def _promote_primary_stats(stats: dict[str, Any], slot_rows: list[dict[str, Any]]) -> None:
    active = [row for row in slot_rows if row.get('generating')]
    primary = active[0] if active else (slot_rows[0] if slot_rows else None)
    if not primary:
        return
    stats['generating'] = bool(primary.get('generating'))
    for key in (
        'generating_seconds',
        'generating_tokens',
        'generating_tokens_per_second',
        'prefill_tokens',
        'prompt_tokens',
        'generation_tokens',
        'tokens_per_second',
    ):
        if primary.get(key) is not None:
            stats[key] = primary[key]
    if stats.get('generating'):
        stats['live_updated_at'] = time.time()
    else:
        _clear_live_stats(stats)
        for key in ('prompt_tokens', 'generation_tokens', 'tokens_per_second'):
            if primary.get(key) is not None:
                stats[key] = primary[key]


def fetch_inference_stats(
    api_url: str,
    *,
    server_id: str = '',
    model_id: str = '',
) -> dict[str, Any]:
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
        'generating_tokens': None,
        'generating_tokens_per_second': None,
        'slots': [],
        'parallel_slots': None,
    }
    sid = str(server_id or '')
    proxy_active = is_proxy_generating(sid)

    if not base:
        last = _LAST_COMPLETION.get(sid) or {}
        for key in ('prompt_tokens', 'generation_tokens', 'tokens_per_second', 'updated_at'):
            if last.get(key) is not None:
                stats[key] = last[key]
        if proxy_active:
            stats['generating'] = True
            started = _proxy_inference_started_at(sid)
            if started is not None:
                stats['generating_seconds'] = round(max(0.0, time.time() - started), 1)
        return stats

    slot_rows: list[dict[str, Any]] = []
    raw_slots: list[dict[str, Any]] = []
    try:
        slots_url = f'{base}/slots'
        model = str(model_id or '').strip()
        if model:
            slots_url = f'{slots_url}?{urlencode({"model": model})}'
        slots_payload = _fetch_json(slots_url)
        raw = slots_payload if isinstance(slots_payload, list) else (slots_payload or {}).get('slots') or []
        raw_slots = [row for row in raw if isinstance(row, dict)]
        stats['parallel_slots'] = len(raw_slots) or None
        any_raw_generating = any(_slot_generation_state(slot)[0] for slot in raw_slots)
        _note_generating_wave(sid, any_generating=any_raw_generating, raw_slots=raw_slots)
        for slot in raw_slots:
            slot_rows.append(_process_slot(sid, slot))
        if raw_slots:
            first = raw_slots[0]
            stats['context_tokens'] = first.get('n_ctx')
            stats['tokens_loaded'] = first.get('n_past') or first.get('n_prompt_tokens_processed')
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        pass

    visible_slots = [row for row in slot_rows if _slot_has_activity(row, server_id=sid)]
    stored = _LAST_COMPLETION_SLOTS.get(sid) or {}
    epoch = _current_wave_epoch(sid)
    seen = {int(row.get('slot_id') or 0) for row in visible_slots}
    for slot_id, entry in stored.items():
        if (_SLOT_WAVE_EPOCH.get(sid) or {}).get(int(slot_id)) != epoch:
            continue
        if int(slot_id) in seen:
            continue
        visible_slots.append({
            'slot_id': int(slot_id),
            'generating': False,
            'generating_seconds': None,
            'generating_tokens': None,
            'generating_tokens_per_second': None,
            'prompt_tokens': entry.get('prompt_tokens'),
            'generation_tokens': entry.get('generation_tokens'),
            'tokens_per_second': entry.get('tokens_per_second'),
        })
        seen.add(int(slot_id))
    visible_slots.sort(key=lambda row: (0 if row.get('generating') else 1, int(row.get('slot_id') or 0)))
    stats['slots'] = visible_slots

    any_generating = any(row.get('generating') for row in slot_rows)
    if any_generating or proxy_active:
        if not any_generating and proxy_active:
            track = _begin_live_track(sid)
            stats['generating'] = True
            stats['live_updated_at'] = time.time()
            started = (
                _proxy_inference_started_at(sid)
                or track.get('started_at')
                or time.time()
            )
            stats['generating_seconds'] = round(max(0.0, time.time() - float(started)), 1)
        else:
            _promote_primary_stats(stats, visible_slots or slot_rows)
            if stats.get('generating'):
                stats['live_updated_at'] = time.time()
    else:
        _end_live_track(sid)
        _promote_primary_stats(stats, visible_slots or slot_rows)

    try:
        props = _fetch_json(f'{base}/props')
        if isinstance(props, dict):
            if stats['context_tokens'] is None:
                stats['context_tokens'] = props.get('default_generation_settings', {}).get('n_ctx')
            total = props.get('total_slots')
            if total is not None:
                stats['parallel_slots'] = int(total)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        pass

    last = _LAST_COMPLETION.get(sid) or {}
    for key in ('prompt_tokens', 'generation_tokens', 'tokens_per_second', 'updated_at'):
        if stats.get(key) is None and last.get(key) is not None:
            stats[key] = last[key]
    if stats.get('tokens_per_second') is not None:
        stats['tokens_per_second'] = round(float(stats['tokens_per_second']), 1)

    if sid:
        cached = dict(stats)
        if not cached.get('generating'):
            _clear_live_stats(cached)
        _STATS_CACHE[sid] = cached
    return stats
