"""Idle maintenance and client-cohort shaping for engines shared by several apps.

Llama-server cannot resize an engine in place: ``(per-slot context, slots)`` is
fixed at process start.  The helper below therefore decides when a reload is
worth paying for:

* **grow** — several different clients have used this engine recently, so the
  next quiet moment is used to give each of them a slot (up to the configured
  cap and what the model can hold).  This is what makes sharing automatic; it
  never touches an engine that is mid-request.
* **shrink** — nobody used the engine for ``context_shrink_idle_seconds``, so
  it is reloaded with a single slot and the same per-slot context.  KV memory
  comes back, and a returning client cannot hit a smaller window.

Everything is conservative: only *slots* change, never the per-slot context of
a running engine, and a pass skips anything with an in-flight request.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 300
DEFAULT_CLIENT_MEMORY_SECONDS = 1800

_LAST_ACTIVE: dict[str, float] = {}
_RECENT_CLIENTS: dict[str, dict[str, float]] = {}


def _now() -> float:
    return time.time()


def reset_idle_tracking() -> None:
    _LAST_ACTIVE.clear()
    _RECENT_CLIENTS.clear()


def note_engine_active(server_id: str, client: str = '') -> None:
    """Record that a client used this engine (kept in memory only)."""
    sid = str(server_id or '').strip()
    if not sid:
        return
    now = _now()
    _LAST_ACTIVE[sid] = now
    key = str(client or '').strip() or 'unknown'
    bucket = _RECENT_CLIENTS.setdefault(sid, {})
    bucket[key] = now


def recent_clients(
    server_id: str,
    *,
    now: float | None = None,
    ttl: float = DEFAULT_CLIENT_MEMORY_SECONDS,
) -> list[str]:
    """Clients that used this engine within ``ttl`` seconds (oldest first)."""
    sid = str(server_id or '').strip()
    clock = float(now if now is not None else _now())
    bucket = _RECENT_CLIENTS.get(sid) or {}
    for key in [key for key, stamp in bucket.items() if clock - stamp > ttl]:
        bucket.pop(key, None)
    return [key for key, _stamp in sorted(bucket.items(), key=lambda row: row[1])]


def idle_seconds_for(server_id: str) -> float | None:
    sid = str(server_id or '').strip()
    seen = _LAST_ACTIVE.get(sid)
    if seen is None:
        return None
    return max(0.0, _now() - seen)


def _engine_shape(server: dict[str, Any], cfg: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """Return ``(per_slot, slots, cap, capability)`` for a loaded chat engine."""
    from core.config import effective_parallel_slots, normalize_load_settings
    from core.model_presets import context_max_for_server
    from core.runtime import loaded_per_slot_context

    load = normalize_load_settings(server.get('load_settings'))
    cap = max(1, int(load.get('parallel_slots') or 1))
    slots = max(1, effective_parallel_slots(server))
    total = int(server.get('context_size') or 0)
    per_slot = loaded_per_slot_context(server) or (total // slots if total else 0)
    if per_slot <= 0:
        return None
    model_cap = context_max_for_server(server, cfg=cfg)
    declared = max(2048, int(server.get('context_max') or cfg.get('context_max') or model_cap))
    capability = int(min(declared, model_cap))
    return per_slot, slots, cap, capability


def _reshape(
    server: dict[str, Any],
    cfg: dict[str, Any],
    *,
    per_slot: int,
    slots: int,
    action: str,
) -> dict[str, Any]:
    """Persist the new shape and reload the engine. Returns a report row."""
    from core.config import get_server, normalize_load_settings, normalize_server, save_config
    from core.server_boot import reload_server

    server_id = str(server.get('id') or '')
    row: dict[str, Any] = {
        'server_id': server_id,
        'action': action,
        'to_slots': int(slots),
        'per_slot': int(per_slot),
        'total': int(per_slot) * int(slots),
    }
    try:
        raw = get_server(cfg, server_id) or {}
        load_settings = normalize_load_settings(raw.get('load_settings'))
        load_settings['requested_parallel_slots'] = int(slots)
        raw['context_size'] = int(per_slot) * int(slots)
        raw['load_settings'] = load_settings
        save_config(cfg)
        result = reload_server(normalize_server(raw), cfg=cfg)
        row['success'] = bool(result.get('success'))
        if not row['success']:
            row['error'] = str(result.get('error') or 'reload failed')
    except Exception as exc:  # noqa: BLE001 - a watchdog must never crash the API
        row['success'] = False
        row['error'] = str(exc)
    logger.info('context maintenance %s', row)
    return row


def auto_shrink_pass(
    *,
    cfg: dict[str, Any] | None = None,
    idle_seconds: float | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Reload idle multi-slot engines into a single slot."""
    from core.client_identity import list_active_clients
    from core.config import is_embedding_server, list_servers, load_config
    from core.inference_stats import is_proxy_generating
    from core.runtime import tcp_port_open

    config = cfg or load_config()
    if config.get('context_auto_shrink') is False:
        return []
    try:
        idle_default = float(
            idle_seconds
            if idle_seconds is not None
            else config.get('context_shrink_idle_seconds') or DEFAULT_IDLE_SECONDS
        )
    except (TypeError, ValueError):
        idle_default = float(DEFAULT_IDLE_SECONDS)
    clock = float(now if now is not None else _now())

    results: list[dict[str, Any]] = []
    for server in list_servers(config):
        if not server.get('enabled', True) or server.get('engine_on') is not True:
            continue
        if is_embedding_server(server):
            continue
        server_id = str(server.get('id') or '').strip()
        if not server_id:
            continue
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        if port <= 0 or not tcp_port_open(host, port):
            continue

        if list_active_clients(server_id) or is_proxy_generating(server_id):
            _LAST_ACTIVE[server_id] = clock
            continue
        last = _LAST_ACTIVE.get(server_id)
        if last is None:
            # First time we see it: start the quiet timer, never shrink blind.
            _LAST_ACTIVE[server_id] = clock
            continue
        if clock - last < idle_default:
            continue
        if len(recent_clients(server_id, now=clock)) > 1:
            # Several apps still consider this engine theirs: keep the slots.
            continue

        shape = _engine_shape(server, config)
        if shape is None:
            continue
        per_slot, slots, _cap, _capability = shape
        if slots <= 1:
            continue
        results.append(_reshape(server, config, per_slot=per_slot, slots=1, action='shrunk_idle'))
        _LAST_ACTIVE[server_id] = clock
    return results


def grow_for_clients_pass(
    *,
    cfg: dict[str, Any] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Give one slot per recently active client, at the next quiet moment."""
    from core.client_identity import list_active_clients
    from core.config import is_embedding_server, list_servers, load_config
    from core.inference_stats import is_proxy_generating
    from core.runtime import tcp_port_open

    config = cfg or load_config()
    if config.get('context_auto_grow_slots') is False:
        return []
    try:
        ttl = float(config.get('context_client_memory_seconds') or DEFAULT_CLIENT_MEMORY_SECONDS)
    except (TypeError, ValueError):
        ttl = float(DEFAULT_CLIENT_MEMORY_SECONDS)
    clock = float(now if now is not None else _now())

    results: list[dict[str, Any]] = []
    for server in list_servers(config):
        if not server.get('enabled', True) or server.get('engine_on') is not True:
            continue
        if is_embedding_server(server):
            continue
        server_id = str(server.get('id') or '').strip()
        if not server_id:
            continue
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        if port <= 0 or not tcp_port_open(host, port):
            continue
        # Growing means a restart: only do it while nothing is generating.
        if list_active_clients(server_id) or is_proxy_generating(server_id):
            continue

        clients = recent_clients(server_id, now=clock, ttl=ttl)
        if len(clients) <= 1:
            continue
        shape = _engine_shape(server, config)
        if shape is None:
            continue
        per_slot, slots, cap, capability = shape
        desired = max(1, min(cap, capability // max(1, per_slot), len(clients)))
        if desired <= slots:
            continue
        results.append(
            _reshape(server, config, per_slot=per_slot, slots=desired, action='grew_for_clients')
        )
    return results


def maintenance_pass(*, cfg: dict[str, Any] | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """Growth first (busy engines get their slots), then idle shrink."""
    rows = grow_for_clients_pass(cfg=cfg, now=now)
    rows.extend(auto_shrink_pass(cfg=cfg, now=now))
    return rows
