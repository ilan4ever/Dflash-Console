"""GPU ownership checks for model loads.

The Console can see models started by other programs, but it must not assume
that an occupied GPU is safe to reuse.  This module keeps the safety decision
in one place and deliberately treats unknown GPU processes as protected.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from core.config import list_servers, normalize_server
from core.gpu_devices import query_gpu_devices


class GpuOwnershipConflict(RuntimeError):
    """A requested GPU is already owned by another runtime or process."""

    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail.get('message') or 'GPU is already in use'))


_OWNERSHIP_LOCK = threading.RLock()
_MIN_PROTECTED_VRAM_GB = 0.25


def _companion_app(label: str) -> bool:
    """OneVoice and Speak share a GPU with a model the user placed there."""
    text = str(label or '').strip().lower()
    return text == 'onevoice' or text.startswith('onevoice ')


def unload_console_engine(server_id: str, *, cfg: dict[str, Any]) -> bool:
    """Unload a model from a configured Console engine, never an external PID."""
    from core.config import get_server
    from core.runtime import build_server_status, unload_model

    server = get_server(cfg, server_id)
    if not server:
        return False
    normalized = normalize_server(dict(server))
    try:
        status = build_server_status(normalized, cfg=cfg, live_probe=True)
    except Exception:
        status = {}
    loaded = status.get('loaded_models') or []
    model_id = str(
        status.get('active_model_id')
        or normalized.get('model_id')
        or (loaded[0] if loaded else '')
        or ''
    ).strip()
    if not model_id:
        return False
    result = unload_model(
        api_url=str(normalized.get('api_url') or ''),
        model_id=model_id,
    )
    return bool(result.get('success'))


def _requested_gpu_indices(
    candidate: dict[str, Any],
    *,
    cfg: dict[str, Any],
    model_id: str = '',
) -> list[int]:
    from core.gpu_devices import resolve_role_gpu_launch_params

    gpus = query_gpu_devices()
    model_hint = str(
        model_id
        or candidate.get('model_id')
        or candidate.get('adhoc_model_path')
        or candidate.get('target_path')
        or candidate.get('id')
        or ''
    )
    launch = resolve_role_gpu_launch_params(
        candidate.get('gpu_device'),
        model_id=model_hint,
        gpus=gpus,
        hardware=cfg.get('hardware_settings'),
        context_size=candidate.get('context_size'),
    )
    main = int(launch.get('main_gpu') or 0)
    if str(launch.get('split_mode') or 'none') == 'layer':
        split_count = len([
            item for item in str(launch.get('tensor_split') or '').split(',')
            if item.strip()
        ])
        if split_count:
            # tensor_split follows the sorted visible GPU list. Use the actual
            # device indexes so sparse device numbering remains safe.
            visible = sorted(int(row.get('index')) for row in gpus if row.get('index') is not None)
            return visible[:split_count] or [main]
    return [main]


def _console_owners(
    *,
    cfg: dict[str, Any],
    target_server_id: str = '',
) -> tuple[list[dict[str, Any]], set[int]]:
    from core.gpu_processes import process_attribution_for_port

    owners: list[dict[str, Any]] = []
    managed_pids: set[int] = set()
    for raw in list_servers(cfg):
        server = normalize_server(dict(raw))
        server_id = str(server.get('id') or '')
        if server_id and server_id == target_server_id:
            continue
        port = int(server.get('port') or 0)
        if port <= 0:
            continue
        try:
            attribution = process_attribution_for_port(
                port,
                str(server.get('host') or '127.0.0.1'),
            )
        except Exception:
            attribution = {}
        pids = {
            int(pid)
            for pid in (attribution.get('pids') or [])
            if str(pid).isdigit() and int(pid) > 0
        }
        managed_pids.update(pids)
        vram_map = attribution.get('vram_by_gpu') or {}
        for gpu_raw, vram_raw in vram_map.items():
            try:
                gpu_index = int(gpu_raw)
                vram_gb = float(vram_raw or 0.0)
            except (TypeError, ValueError):
                continue
            if vram_gb < _MIN_PROTECTED_VRAM_GB:
                continue
            owners.append({
                'gpu_index': gpu_index,
                'owner_type': 'console',
                'owner_id': server_id,
                'owner_label': str(server.get('label') or server_id or 'DFlash engine'),
                'pid': min(pids) if pids else None,
                'vram_gb': round(vram_gb, 2),
                'source': 'DFlash Console',
                'protected': True,
            })
    return owners, managed_pids


def _external_owners(
    *,
    cfg: dict[str, Any],
    servers: list[dict[str, Any]],
    managed_pids: set[int],
) -> list[dict[str, Any]]:
    from core.gpu_processes import get_external_gpu_loads, query_compute_apps

    owners: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    companion_pids: set[int] = set()
    try:
        cards = get_external_gpu_loads(
            servers=servers,
            cfg=cfg,
            fast=True,
        )
    except Exception:
        cards = []
    for card in cards:
        try:
            gpu_index = int(card.get('gpu_index'))
            pid = int(card.get('pid') or 0)
            vram_gb = float(card.get('vram_gb') or 0.0)
        except (TypeError, ValueError):
            continue
        if pid in managed_pids or vram_gb < _MIN_PROTECTED_VRAM_GB:
            continue
        key = (gpu_index, pid)
        if key in seen:
            continue
        seen.add(key)
        label = str(
            card.get('source_label')
            or card.get('app_label')
            or card.get('model_name')
            or card.get('process_name')
            or 'External GPU process'
        )
        if _companion_app(label):
            if pid:
                companion_pids.add(pid)
            continue
        strata = str(card.get('runtime_id') or '').strip().lower() == 'strata'
        owners.append({
            'gpu_index': gpu_index,
            'owner_type': 'strata' if strata else 'external',
            'owner_id': str(card.get('id') or pid),
            'owner_label': label,
            'pid': pid or None,
            'vram_gb': round(vram_gb, 2),
            'source': 'Strata' if strata else 'External application',
            'protected': True,
        })

    # If a foreign process cannot be identified as a model card, it is still
    # protected. This prevents a new load from racing a CUDA process during
    # startup or while another app has no API endpoint.
    try:
        compute_rows = query_compute_apps()
    except Exception:
        compute_rows = []
    known_pids = {int(owner.get('pid') or 0) for owner in owners} | companion_pids
    for row in compute_rows:
        try:
            pid = int(row.get('pid') or 0)
            gpu_index = int(row.get('gpu_index'))
            vram_gb = float(row.get('vram_gb') or 0.0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in managed_pids or pid in known_pids or vram_gb < _MIN_PROTECTED_VRAM_GB:
            continue
        owners.append({
            'gpu_index': gpu_index,
            'owner_type': 'unknown',
            'owner_id': str(pid),
            'owner_label': str(row.get('process_name') or 'Unknown GPU process'),
            'pid': pid,
            'vram_gb': round(vram_gb, 2),
            'source': 'Unidentified GPU process',
            'protected': True,
        })
    return owners


def inspect_gpu_ownership(
    candidate: dict[str, Any],
    *,
    cfg: dict[str, Any],
    target_server_id: str = '',
    model_id: str = '',
) -> dict[str, Any]:
    """Return the requested devices and protected owners currently on them."""
    requested = _requested_gpu_indices(candidate, cfg=cfg, model_id=model_id)
    console, managed_pids = _console_owners(cfg=cfg, target_server_id=target_server_id)
    servers = [normalize_server(dict(row)) for row in list_servers(cfg)]
    external = _external_owners(cfg=cfg, servers=servers, managed_pids=managed_pids)
    conflicts = [
        owner for owner in [*console, *external]
        if int(owner.get('gpu_index') or -1) in requested
    ]
    return {
        'requested_gpu_indices': requested,
        'conflicts': conflicts,
        'owners': [*console, *external],
    }


def enforce_gpu_ownership(
    candidate: dict[str, Any],
    *,
    cfg: dict[str, Any],
    target_server_id: str = '',
    model_id: str = '',
    unload_console_engine: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Enforce the configured ownership policy before a model load.

    ``auto_switch`` is intentionally narrow: only Console-owned engines may be
    unloaded. Strata, SSH-tunneled services, unknown processes, and every
    other external owner remain protected.
    """
    policy = str((cfg.get('hardware_settings') or {}).get('gpu_ownership_policy') or 'reject').strip().lower()
    if policy not in ('reject', 'auto_switch'):
        policy = 'reject'
    with _OWNERSHIP_LOCK:
        state = inspect_gpu_ownership(
            candidate,
            cfg=cfg,
            target_server_id=target_server_id,
            model_id=model_id,
        )
        conflicts = list(state.get('conflicts') or [])
        released: list[str] = []
        if conflicts and policy == 'auto_switch' and unload_console_engine:
            console_ids = sorted({
                str(row.get('owner_id') or '')
                for row in conflicts
                if row.get('owner_type') == 'console' and str(row.get('owner_id') or '')
            })
            protected = [row for row in conflicts if row.get('owner_type') != 'console']
            if not protected:
                for server_id in console_ids:
                    try:
                        if unload_console_engine(server_id):
                            released.append(server_id)
                    except Exception:
                        continue
                if released:
                    state = inspect_gpu_ownership(
                        candidate,
                        cfg=cfg,
                        target_server_id=target_server_id,
                        model_id=model_id,
                    )
                    conflicts = list(state.get('conflicts') or [])
        if conflicts:
            labels = ', '.join(
                f"{row.get('owner_label') or 'GPU process'} on GPU {row.get('gpu_index')}"
                for row in conflicts
            )
            detail = {
                'error': 'gpu_ownership_conflict',
                'message': (
                    f'GPU ownership conflict: {labels}. '
                    'Unload the existing model or choose another GPU.'
                ),
                'policy': policy,
                'requested_gpu_indices': state.get('requested_gpu_indices') or [],
                'conflicts': conflicts,
                'released_console_engines': released,
                'retryable': True,
            }
            raise GpuOwnershipConflict(detail)
        return {
            'policy': policy,
            'requested_gpu_indices': state.get('requested_gpu_indices') or [],
            'released_console_engines': released,
        }
