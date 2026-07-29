"""Automatic VRAM checks before engine loads — no user-facing mode toggle."""

from __future__ import annotations

from typing import Any

from core.config import normalize_hardware_settings, normalize_load_settings
from core.gpu_devices import get_gpu_devices_payload
from core.model_stack import resolve_model_stack
from core.system_stats import get_system_stats_payload


def _enabled_gpu_indices(cfg: dict[str, Any]) -> list[int]:
    hw = normalize_hardware_settings(cfg.get('hardware_settings'))
    enabled = hw.get('enabled_gpu_indices') or []
    if enabled:
        return [int(i) for i in enabled]
    gpus = get_gpu_devices_payload().get('gpus') or []
    return [int(g['index']) for g in gpus if g.get('index') is not None]


def _vram_budget(cfg: dict[str, Any]) -> tuple[float, float, int]:
    """Return (free_gb, total_gb, gpu_count) for enabled GPUs."""
    indices = set(_enabled_gpu_indices(cfg))
    stats = get_system_stats_payload()
    free_gb = 0.0
    total_gb = 0.0
    count = 0
    for gpu in stats.get('gpus') or []:
        index = gpu.get('index')
        if index is None or int(index) not in indices:
            continue
        total = float(gpu.get('vram_total_gb') or 0)
        used = float(gpu.get('vram_used_gb') or 0)
        if total <= 0:
            continue
        total_gb += total
        free_gb += max(0.0, total - used)
        count += 1
    if count == 0:
        devices = get_gpu_devices_payload().get('gpus') or []
        for gpu in devices:
            if int(gpu.get('index', -1)) not in indices:
                continue
            vram = float(gpu.get('vram_gb') or 0)
            if vram > 0:
                total_gb += vram
                free_gb += vram * 0.85
                count += 1
    return round(free_gb, 2), round(total_gb, 2), count


def _estimate_load_gb(server: dict[str, Any], cfg: dict[str, Any]) -> float:
    adhoc_path = str(server.get('adhoc_model_path') or '').strip()
    if adhoc_path:
        from pathlib import Path

        try:
            weights_gb = round(Path(adhoc_path).stat().st_size / (1024 ** 3), 2)
        except OSError:
            weights_gb = 0.0
    else:
        stack = resolve_model_stack(server, cfg=cfg)
        weights_gb = 0.0
        for row in stack:
            path = row.get('path')
            if not path:
                continue
            size = row.get('size_gb')
            if size is None:
                from pathlib import Path

                try:
                    size = round(Path(str(path)).stat().st_size / (1024 ** 3), 2)
                except OSError:
                    size = 0.0
            weights_gb += float(size or 0)

    context = max(2048, int(server.get('context_size') or 8192))
    load = normalize_load_settings(server.get('load_settings'))
    gpu_layers = int(load.get('gpu_layers') or 99)
    on_gpu = min(1.0, max(0.05, gpu_layers / 99.0 if gpu_layers < 99 else 1.0))
    kv_gb = round((context / 8192) * 0.4, 2)
    gpu_weights = weights_gb * on_gpu
    cpu_weights = weights_gb * (1.0 - on_gpu) * 0.25
    return round(gpu_weights + kv_gb + cpu_weights, 2)


def assess_load(server: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Smart automatic guardrails: block only when load clearly exceeds VRAM."""
    estimated = _estimate_load_gb(server, cfg)
    free_gb, total_gb, gpu_count = _vram_budget(cfg)

    if gpu_count <= 0:
        return {
            'level': 'warn',
            'message': 'No enabled NVIDIA GPU detected. The engine may run on CPU only and load slowly.',
            'estimated_gb': estimated,
            'free_gb': free_gb,
            'total_gb': total_gb,
        }

    if total_gb <= 0:
        return {
            'level': 'ok',
            'message': '',
            'estimated_gb': estimated,
            'free_gb': free_gb,
            'total_gb': total_gb,
        }

    if estimated > total_gb * 1.08:
        return {
            'level': 'block',
            'message': (
                f'This checkpoint needs about {estimated} GB on GPU, but enabled GPUs '
                f'only have {total_gb} GB total. Lower context, reduce GPU layers, or pick a smaller quant.'
            ),
            'estimated_gb': estimated,
            'free_gb': free_gb,
            'total_gb': total_gb,
        }

    if estimated > free_gb * 0.92:
        return {
            'level': 'warn',
            'message': (
                f'VRAM looks tight (~{estimated} GB needed, ~{free_gb} GB free). '
                'Close other GPU apps if the load fails.'
            ),
            'estimated_gb': estimated,
            'free_gb': free_gb,
            'total_gb': total_gb,
        }

    return {
        'level': 'ok',
        'message': '',
        'estimated_gb': estimated,
        'free_gb': free_gb,
        'total_gb': total_gb,
    }
