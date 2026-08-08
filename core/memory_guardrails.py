"""Automatic VRAM checks before engine loads — no user-facing mode toggle."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.config import normalize_hardware_settings, normalize_load_settings
from core.gpu_devices import get_gpu_devices_payload, resolve_role_gpu_launch_params
from core.model_stack import resolve_model_stack
from core.system_stats import get_system_stats_payload

_MODEL_SHARD_RE = re.compile(
    r'^(?P<prefix>.+?)(?:[-_])\d{5}-of-\d{5}(?P<suffix>\.[^.]+)$',
    re.IGNORECASE,
)
_VRAM_HEADROOM_GB = 1.0


def _enabled_gpu_indices(cfg: dict[str, Any]) -> list[int]:
    hw = normalize_hardware_settings(cfg.get('hardware_settings'))
    enabled = hw.get('enabled_gpu_indices') or []
    if enabled:
        return [int(i) for i in enabled]
    gpus = get_gpu_devices_payload().get('gpus') or []
    return [int(g['index']) for g in gpus if g.get('index') is not None]


def _path_size_gb(path: str | Path) -> float:
    """Return a model's on-disk size, including all GGUF shard files."""
    candidate = Path(path)
    try:
        if candidate.is_dir():
            return round(
                sum(item.stat().st_size for item in candidate.rglob('*') if item.is_file())
                / (1024 ** 3),
                2,
            )
        if not candidate.is_file():
            return 0.0
        match = _MODEL_SHARD_RE.match(candidate.name)
        if match:
            pattern = re.compile(
                rf'^{re.escape(match.group("prefix"))}(?:[-_])\d{{5}}-of-\d{{5}}'
                rf'{re.escape(match.group("suffix"))}$',
                re.IGNORECASE,
            )
            shard_total = sum(
                item.stat().st_size
                for item in candidate.parent.iterdir()
                if item.is_file() and pattern.match(item.name)
            )
            if shard_total:
                return round(shard_total / (1024 ** 3), 2)
        return round(candidate.stat().st_size / (1024 ** 3), 2)
    except OSError:
        return 0.0


def _component_size_gb(row: dict[str, Any]) -> float:
    metadata_size = 0.0
    try:
        metadata_size = max(0.0, float(row.get('size_gb') or 0.0))
    except (TypeError, ValueError):
        pass
    return max(metadata_size, _path_size_gb(str(row.get('path') or '')))


def _load_components(server: dict[str, Any], cfg: dict[str, Any]) -> dict[str, float]:
    """Find target/draft sizes without losing zero-byte GGUF shard headers."""
    adhoc_path = str(server.get('adhoc_model_path') or '').strip()
    if adhoc_path:
        return {
            'target_gb': _path_size_gb(adhoc_path),
            'draft_gb': 0.0,
        }

    target_gb = 0.0
    draft_gb = 0.0
    try:
        stack = resolve_model_stack(server, cfg=cfg)
    except (OSError, TypeError, ValueError):
        stack = []
    for row in stack:
        if not isinstance(row, dict) or not row.get('path'):
            continue
        size_gb = _component_size_gb(row)
        role = str(row.get('role') or '').lower()
        if role.startswith('draft'):
            draft_gb += size_gb
        else:
            target_gb += size_gb
    return {
        'target_gb': round(target_gb, 2),
        'draft_gb': round(draft_gb, 2),
    }


def _gpu_snapshot(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enabled GPUs with current free VRAM in the launcher's field names."""
    indices = set(_enabled_gpu_indices(cfg))
    stats = get_system_stats_payload()
    detected = {
        int(gpu.get('index')): gpu
        for gpu in get_gpu_devices_payload().get('gpus') or []
        if gpu.get('index') is not None
    }
    result: list[dict[str, Any]] = []
    for raw in stats.get('gpus') or []:
        try:
            index = int(raw.get('index'))
        except (TypeError, ValueError):
            continue
        if index not in indices:
            continue
        total = float(raw.get('vram_total_gb') or 0.0)
        used = float(raw.get('vram_used_gb') or 0.0)
        if total <= 0:
            continue
        result.append({
            'index': index,
            'name': raw.get('name') or detected.get(index, {}).get('name') or f'GPU {index}',
            'display_name': detected.get(index, {}).get('display_name') or raw.get('display_name') or f'GPU {index}',
            'vram_gb': total,
            'vram_free_gb': round(max(0.0, total - used), 2),
        })
    if result:
        return result
    for raw in detected.values():
        try:
            index = int(raw.get('index'))
            total = float(raw.get('vram_gb') or 0.0)
        except (TypeError, ValueError):
            continue
        if index in indices and total > 0:
            result.append({
                **raw,
                'vram_free_gb': round(total * 0.85, 2),
            })
    return sorted(result, key=lambda item: int(item['index']))


def _vram_budget(cfg: dict[str, Any]) -> tuple[float, float, int]:
    """Return (free_gb, total_gb, gpu_count) for enabled GPUs."""
    devices = _gpu_snapshot(cfg)
    return (
        round(sum(float(item.get('vram_free_gb') or 0.0) for item in devices), 2),
        round(sum(float(item.get('vram_gb') or 0.0) for item in devices), 2),
        len(devices),
    )


def _estimate_load_gb(server: dict[str, Any], cfg: dict[str, Any]) -> float:
    components = _load_components(server, cfg)
    weights_gb = components['target_gb'] + components['draft_gb']
    context = max(2048, int(server.get('context_size') or 8192))
    load = normalize_load_settings(server.get('load_settings'))
    gpu_layers = int(load.get('gpu_layers') or 99)
    on_gpu = min(1.0, max(0.0, gpu_layers / 99.0))
    kv_gb = round((context / 8192) * 0.4, 2)
    gpu_weights = components['target_gb'] * on_gpu + components['draft_gb']
    cpu_weights = weights_gb * (1.0 - on_gpu) * 0.25
    return round(gpu_weights + kv_gb + cpu_weights, 2)


def _load_plan(server: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    components = _load_components(server, cfg)
    target_gb = components['target_gb']
    draft_gb = components['draft_gb']
    weights_gb = round(target_gb + draft_gb, 2)
    context = max(2048, int(server.get('context_size') or 8192))
    load = normalize_load_settings(server.get('load_settings'))
    gpu_layers = int(load.get('gpu_layers') or 99)
    gpu_fraction = min(1.0, max(0.0, gpu_layers / 99.0))
    gpu_weights_gb = round((target_gb * gpu_fraction) + draft_gb, 2)
    kv_cache_gb = round((context / 8192) * 0.4, 2)
    hardware = normalize_hardware_settings(cfg.get('hardware_settings'))
    kv_on_gpu = hardware.get('offload_kv_cache_to_gpu') is not False
    gpu_kv_gb = kv_cache_gb if kv_on_gpu else 0.0
    cpu_kv_gb = 0.0 if kv_on_gpu else kv_cache_gb
    devices = _gpu_snapshot(cfg)
    free_gb = round(sum(float(item.get('vram_free_gb') or 0.0) for item in devices), 2)
    total_gb = round(sum(float(item.get('vram_gb') or 0.0) for item in devices), 2)
    model_hint = str(server.get('model_id') or server.get('label') or 'model')
    launch = resolve_role_gpu_launch_params(
        server.get('gpu_device'),
        model_id=model_hint,
        gpus=devices,
        hardware=hardware,
        context_size=context,
    )
    selected = {
        int(item['index']): item
        for item in devices
        if item.get('index') is not None
    }
    split_mode = str(launch.get('split_mode') or 'none').lower()
    main_gpu = int(launch.get('main_gpu') or 0)
    allocations: list[dict[str, Any]] = []

    if split_mode == 'none' or len(selected) <= 1:
        device = selected.get(main_gpu)
        if device is not None:
            allocations.append({
                **device,
                'required_gb': round(gpu_weights_gb + gpu_kv_gb, 2),
            })
    else:
        raw_shares = [
            float(part.strip())
            for part in str(launch.get('tensor_split') or '').split(',')
            if part.strip()
        ]
        ordered = [selected[index] for index in sorted(selected)]
        if len(raw_shares) != len(ordered) or sum(raw_shares) <= 0:
            raw_shares = [float(item.get('vram_gb') or 0.0) for item in ordered]
        share_total = sum(raw_shares) or 1.0
        for device, raw_share in zip(ordered, raw_shares):
            share = max(0.0, raw_share / share_total)
            allocations.append({
                **device,
                'required_gb': round((gpu_weights_gb + gpu_kv_gb) * share, 2),
            })

    for allocation in allocations:
        allocation['headroom_gb'] = round(
            float(allocation.get('vram_free_gb') or 0.0)
            - float(allocation.get('required_gb') or 0.0),
            2,
        )
    fits = bool(allocations) and all(
        float(item.get('required_gb') or 0.0) + _VRAM_HEADROOM_GB
        <= float(item.get('vram_free_gb') or 0.0)
        for item in allocations
    )
    required_gpu_gb = round(
        max((float(item.get('required_gb') or 0.0) for item in allocations), default=0.0),
        2,
    )
    usage_ratio = max(
        (
            float(item.get('required_gb') or 0.0)
            / max(float(item.get('vram_free_gb') or 0.0), 0.01)
            for item in allocations
        ),
        default=0.0,
    )
    return {
        'target_gb': target_gb,
        'draft_gb': draft_gb,
        'weights_gb': weights_gb,
        'gpu_weights_gb': gpu_weights_gb,
        'kv_cache_gb': kv_cache_gb,
        'gpu_kv_gb': gpu_kv_gb,
        'cpu_kv_gb': cpu_kv_gb,
        'gpu_layers': gpu_layers,
        'gpu_fraction': round(gpu_fraction, 3),
        'context_size': context,
        'free_gb': free_gb,
        'total_gb': total_gb,
        'gpu_count': len(devices),
        'main_gpu': main_gpu,
        'split_mode': split_mode,
        'tensor_split': str(launch.get('tensor_split') or ''),
        'gpu_required_gb': required_gpu_gb,
        'usage_ratio': round(usage_ratio, 3),
        'fits': fits,
        'kv_on_gpu': kv_on_gpu,
        'allocations': allocations,
    }


def _memory_message(plan: dict[str, Any], *, level: str) -> str:
    allocations = plan.get('allocations') or []
    model_name = Path(str(plan.get('model_name') or 'model')).name
    weights = float(plan.get('weights_gb') or 0.0)
    gpu_weights = float(plan.get('gpu_weights_gb') or 0.0)
    gpu_kv = float(plan.get('gpu_kv_gb') or 0.0)
    kv_location = 'GPU VRAM' if plan.get('kv_on_gpu') else 'system RAM'
    requirement = float(plan.get('gpu_required_gb') or 0.0)
    if len(allocations) == 1:
        device = allocations[0]
        gpu_label = device.get('display_name') or f"GPU {device.get('index')}"
        detail = (
            f'GPU {device.get("index")} ({gpu_label}) has '
            f'{float(device.get("vram_free_gb") or 0.0):.1f} GB free'
        )
    else:
        detail = '; '.join(
            f'GPU {item.get("index")} needs {float(item.get("required_gb") or 0.0):.1f} GB '
            f'but has {float(item.get("vram_free_gb") or 0.0):.1f} GB free'
            for item in allocations
        )

    prefix = 'Cannot load' if level == 'block' else 'VRAM warning for'
    weight_detail = f'{gpu_weights:.1f} GB model weights on GPU'
    if gpu_weights + 0.05 < weights:
        weight_detail += f' ({weights:.1f} GB total; remaining layers use system RAM)'
    message = (
        f'{prefix} {model_name}: about {requirement:.1f} GB of GPU memory is required '
        f'({weight_detail} + {gpu_kv:.1f} GB KV cache on {kv_location}), {detail}.'
    )
    if level == 'block':
        if str(plan.get('split_mode') or 'none') == 'none':
            message += ' The current launch uses one GPU only.'
            if float(plan.get('free_gb') or 0.0) + _VRAM_HEADROOM_GB < requirement:
                message += (
                    f' All enabled GPUs currently have only {float(plan.get("free_gb") or 0.0):.1f} GB '
                    'free in total, so splitting also requires unloading other GPU models.'
                )
        message += (
            ' Unload another GPU model, lower GPU layers or context, '
            'disable GPU KV offload to use system RAM, or enable multi-GPU splitting.'
        )
    else:
        message += ' Close other GPU apps before loading if possible.'
    return message


def assess_load(server: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Estimate the selected launch, including strategy and current free VRAM."""
    plan = _load_plan(server, cfg)
    plan['model_name'] = Path(
        str(server.get('adhoc_model_path') or server.get('model_id') or server.get('label') or 'model')
    ).name
    if int(plan.get('gpu_count') or 0) <= 0:
        plan.update({
            'level': 'warn',
            'message': 'No enabled NVIDIA GPU detected. The engine may run on CPU only and load slowly.',
        })
    elif not plan.get('allocations'):
        plan.update({
            'level': 'block',
            'message': 'The selected GPU is not available. Enable a detected GPU or choose another engine device.',
        })
    elif not plan.get('fits'):
        plan.update({
            'level': 'block',
            'message': _memory_message(plan, level='block'),
        })
    elif float(plan.get('usage_ratio') or 0.0) >= 0.85:
        plan.update({
            'level': 'warn',
            'message': _memory_message(plan, level='warn'),
        })
    else:
        plan.update({
            'level': 'ok',
            'message': '',
        })
    plan['estimated_gb'] = plan.get('gpu_required_gb', 0.0)
    return plan
