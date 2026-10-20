"""Idle maintenance for engines shared by several apps.

Llama-server cannot resize an engine in place: ``(per-slot context, slots)`` is
fixed at process start.  So the Console has to occasionally reload an engine
into a smaller shape when nobody is using it — that is the only way to give the
KV memory back while keeping busy apps untouched.

The pass below is deliberately conservative:

* it only ever *shrinks* (never grows; growth is request driven),
* it only touches engines with more than one slot,
* it never touches an engine with an in-flight request,
* it waits ``context_shrink_idle_seconds`` (default 300 s) of quiet first,
* it keeps the per-slot context the engine already has, so a client returning
  later cannot suddenly hit a smaller window.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 300
_LAST_ACTIVE: dict[str, float] = {}


def _now() -> float:
    return time.time()


def reset_idle_tracking() -> None:
    _LAST_ACTIVE.clear()


def note_engine_active(server_id: str) -> None:
    """Record that a client used this engine (kept in memory only)."""
    sid = str(server_id or '').strip()
    if sid:
        _LAST_ACTIVE[sid] = _now()


def idle_seconds_for(server_id: str) -> float | None:
    sid = str(server_id or '').strip()
    seen = _LAST_ACTIVE.get(sid)
    if seen is None:
        return None
    return max(0.0, _now() - seen)


def auto_shrink_pass(
    *,
    cfg: dict[str, Any] | None = None,
    idle_seconds: float | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Reload idle multi-slot engines into a single slot.

    Returns one row per engine that was reloaded (or that failed to reload).
    """
    from core.client_identity import list_active_clients
    from core.config import is_embedding_server, list_servers, load_config, normalize_load_settings
    from core.context_policy import plan_context
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
        load = normalize_load_settings(server.get('load_settings'))
        current_slots = max(1, int(load.get('parallel_slots') or 1))
        if current_slots <= 1:
            _LAST_ACTIVE.setdefault(server_id, clock)
            continue
        host = str(server.get('host') or '127.0.0.1')
        port = int(server.get('port') or 0)
        if port <= 0 or not tcp_port_open(host, port):
            continue

        active = list_active_clients(server_id)
        if active or is_proxy_generating(server_id):
            _LAST_ACTIVE[server_id] = clock
            continue

        last = _LAST_ACTIVE.get(server_id)
        if last is None:
            # First time we see it: start the quiet timer, never shrink blind.
            _LAST_ACTIVE[server_id] = clock
            continue
        if clock - last < idle_default:
            continue

        from core.config import save_config
        from core.model_presets import context_max_for_server
        from core.runtime import loaded_per_slot_context
        from core.server_boot import reload_server

        model_cap = context_max_for_server(server, cfg=config)
        declared_cap = max(
            2048,
            int(server.get('context_max') or config.get('context_max') or model_cap),
        )
        capability = int(min(declared_cap, model_cap))
        current_per_slot = loaded_per_slot_context(server) or int(server.get('context_size') or 0)
        plan = plan_context(
            clients=1,
            max_slots=current_slots,
            capability=capability,
            current_per_slot=current_per_slot,
            current_slots=current_slots,
            idle_shrink=True,
        )
        if not plan.needs_reload:
            continue

        row: dict[str, Any] = {
            'server_id': server_id,
            'action': 'shrunk_idle',
            'from_slots': current_slots,
            'to_slots': int(plan.slots),
            'per_slot': int(plan.per_slot),
            'total': int(plan.total),
        }
        try:
            from core.config import get_server, normalize_server

            raw = get_server(config, server_id) or {}
            load_settings = dict(raw.get('load_settings') or {})
            load_settings['parallel_slots'] = int(plan.slots)
            raw['context_size'] = int(plan.total)
            raw['load_settings'] = load_settings
            save_config(config)
            reload_row = reload_server(normalize_server(raw), cfg=config)
            row['success'] = bool(reload_row.get('success'))
            if not row['success']:
                row['error'] = str(reload_row.get('error') or 'reload failed')
        except Exception as exc:  # noqa: BLE001 - a watchdog must never crash
            row['success'] = False
            row['error'] = str(exc)
        _LAST_ACTIVE[server_id] = clock
        logger.info('context auto-shrink %s', row)
        results.append(row)
    return results
