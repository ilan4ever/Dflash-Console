"""GPU discovery and launch resolution for llama-server."""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Any

from core.config import DEFAULT_HARDWARE_SETTINGS, normalize_hardware_settings


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'startupinfo': startupinfo, 'creationflags': flags}
    return {}


def _query_gpus() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            [
                'nvidia-smi',
                '--query-gpu=index,name,memory.total,memory.used,memory.free',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []

    gpus: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 2:
            continue
        entry: dict[str, str] = {'index': parts[0], 'name': parts[1]}
        if len(parts) > 2 and parts[2].replace('.', '', 1).isdigit():
            entry['vram_gb'] = f'{round(float(parts[2]) / 1024, 2)}'
        if len(parts) > 3 and parts[3].replace('.', '', 1).isdigit():
            entry['vram_used_gb'] = f'{round(float(parts[3]) / 1024, 2)}'
        if len(parts) > 4 and parts[4].replace('.', '', 1).isdigit():
            entry['vram_free_gb'] = f'{round(float(parts[4]) / 1024, 2)}'
        gpus.append(entry)
    return gpus


def format_gpu_display_name(name: str, gpu_index: int | None = None) -> str:
    lower = str(name or '').lower()
    if 'titan' in lower:
        return 'TITAN'
    if '4090' in lower or re.search(r'geforce\s*rtx\s*40', lower):
        return 'RTX 4090'
    cleaned = re.sub(r'^(nvidia|geforce)\s+', '', str(name or '').strip(), flags=re.I).strip()
    if cleaned:
        return cleaned if len(cleaned) <= 24 else cleaned[:21] + '…'
    if gpu_index is not None:
        return f'GPU {int(gpu_index) + 1}'
    return 'GPU'


def query_gpu_devices() -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for entry in _query_gpus():
        try:
            index = int(str(entry.get('index') or '').strip())
        except (TypeError, ValueError):
            continue
        name = str(entry.get('name') or '').strip() or f'GPU {index + 1}'
        vram_raw = str(entry.get('vram_gb') or '').strip()
        vram_gb = float(vram_raw) if vram_raw else None
        used_raw = str(entry.get('vram_used_gb') or '').strip()
        free_raw = str(entry.get('vram_free_gb') or '').strip()
        devices.append({
            'index': index,
            'name': name,
            'display_name': format_gpu_display_name(name, index),
            'vram_gb': vram_gb,
            'vram_used_gb': float(used_raw) if used_raw else None,
            'vram_free_gb': float(free_raw) if free_raw else None,
        })
    devices.sort(key=lambda item: item['index'])
    return devices


def _filter_enabled_gpus(
    devices: list[dict[str, Any]],
    hardware: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    hw = normalize_hardware_settings(hardware)
    enabled = hw.get('enabled_gpu_indices') or []
    if not enabled:
        return devices
    enabled_set = {int(item) for item in enabled}
    filtered = [item for item in devices if int(item['index']) in enabled_set]
    return filtered or devices


def _model_looks_large(model_id: str) -> bool:
    lower = str(model_id or '').lower()
    return any(token in lower for token in ('31b', '70b', '32b', '27b', '30b', '34b', '405b'))


def _gpu_preference_score(device: dict[str, Any]) -> tuple[float, float]:
    """Prefer fast cards (4090) over slower high-VRAM cards (TITAN)."""
    name = str(device.get('name') or '').lower()
    speed = 0.0
    if '4090' in name:
        speed = 1000.0
    elif '4080' in name:
        speed = 900.0
    elif '4070' in name:
        speed = 800.0
    elif 'titan rtx' in name:
        speed = 420.0
    elif 'titan' in name:
        speed = 400.0
    elif 'rtx' in name:
        speed = 600.0
    vram = float(device.get('vram_gb') or 0.0)
    return (speed, vram)


def estimate_model_vram_gb(model_id: str = '', *, context_size: int | None = None) -> float:
    """Rough VRAM need so Console can spill a second engine off the 4090."""
    lower = str(model_id or '').lower()
    weights = 12.0
    match = re.search(r'(\d+)\s*b', lower)
    if match:
        try:
            weights = float(match.group(1))
        except ValueError:
            weights = 12.0
    # Q4/QAT-ish weights + activations; KV grows with context.
    base = max(4.0, weights * 0.65)
    ctx = max(0, int(context_size or 0))
    kv = 0.0
    if ctx >= 65536:
        kv = 6.0 if weights >= 27 else 3.5
    elif ctx >= 32768:
        kv = 3.5 if weights >= 27 else 2.0
    elif ctx >= 8192:
        kv = 1.5
    return base + kv


def _pick_single_gpu(
    devices: list[dict[str, Any]],
    *,
    model_id: str = '',
    context_size: int | None = None,
) -> int:
    ranked = sorted(devices, key=_gpu_preference_score, reverse=True)
    preferred = ranked[0]
    needed = estimate_model_vram_gb(model_id, context_size=context_size)
    free_pref = preferred.get('vram_free_gb')
    # Keep the fast card when it still has room (or free VRAM unknown).
    if free_pref is None or float(free_pref) >= needed:
        return int(preferred['index'])
    # Big-job spill: only when the preferred GPU is too full.
    for alt in ranked[1:]:
        free_alt = alt.get('vram_free_gb')
        if free_alt is not None and float(free_alt) >= needed:
            return int(alt['index'])
    return int(preferred['index'])


def resolve_auto_gpu_launch(
    model_id: str = '',
    gpus: list[dict[str, Any]] | None = None,
    hardware: dict[str, Any] | None = None,
    *,
    context_size: int | None = None,
) -> dict[str, Any]:
    hw = normalize_hardware_settings(hardware)
    devices = _filter_enabled_gpus(list(gpus or query_gpu_devices()), hw)
    if not devices:
        return {'main_gpu': 0, 'split_mode': 'none', 'tensor_split': ''}

    main_gpu = _pick_single_gpu(devices, model_id=model_id, context_size=context_size)
    strategy = str(hw.get('gpu_strategy') or DEFAULT_HARDWARE_SETTINGS['gpu_strategy'])

    # Default / recommended: never layer-split — PCIe sync kills tok/s.
    if strategy == 'single_largest' or len(devices) == 1:
        return {'main_gpu': main_gpu, 'split_mode': 'none', 'tensor_split': ''}

    should_split = len(devices) >= 2 and (
        strategy == 'split_evenly'
        or (strategy == 'split_by_vram' and _model_looks_large(model_id))
    )
    if not should_split:
        return {'main_gpu': main_gpu, 'split_mode': 'none', 'tensor_split': ''}

    by_index = sorted(devices, key=lambda item: item['index'])
    if strategy == 'split_evenly':
        weights = [1.0 / len(by_index)] * len(by_index)
    else:
        total_vram = sum(max(float(item.get('vram_gb') or 0.0), 0.1) for item in by_index)
        weights = [
            max(float(item.get('vram_gb') or 0.0), 0.1) / total_vram
            for item in by_index
        ]
    return {
        'main_gpu': main_gpu,
        'split_mode': 'layer',
        'tensor_split': ','.join(f'{weight:.4f}' for weight in weights),
    }


def resolve_role_gpu_launch_params(
    gpu_device: str | None,
    *,
    model_id: str = '',
    gpus: list[dict[str, Any]] | None = None,
    hardware: dict[str, Any] | None = None,
    context_size: int | None = None,
) -> dict[str, Any]:
    raw = str(gpu_device or 'auto').strip().lower()
    if raw in ('', 'auto', 'automatic'):
        auto = resolve_auto_gpu_launch(
            model_id,
            gpus,
            hardware,
            context_size=context_size,
        )
        return {
            'gpu_device': 'auto',
            'main_gpu': int(auto['main_gpu']),
            'split_mode': str(auto['split_mode'] or 'none'),
            'tensor_split': str(auto.get('tensor_split') or ''),
        }

    try:
        main_gpu = max(0, int(raw))
    except (TypeError, ValueError):
        auto = resolve_auto_gpu_launch(
            model_id,
            gpus,
            hardware,
            context_size=context_size,
        )
        return {
            'gpu_device': 'auto',
            'main_gpu': int(auto['main_gpu']),
            'split_mode': str(auto['split_mode'] or 'none'),
            'tensor_split': str(auto.get('tensor_split') or ''),
        }

    return {
        'gpu_device': str(main_gpu),
        'main_gpu': main_gpu,
        'split_mode': 'none',
        'tensor_split': '',
    }


def format_gpu_assignment(gpu_device: str, launch: dict[str, Any], gpus: list[dict[str, Any]]) -> str:
    devices = {int(item['index']): item for item in gpus if isinstance(item, dict)}
    main_gpu = int(launch.get('main_gpu') or 0)
    main_name = devices.get(main_gpu, {}).get('display_name') or format_gpu_display_name('', main_gpu)
    raw = str(gpu_device or 'auto').strip().lower()
    if raw in ('', 'auto', 'automatic'):
        if str(launch.get('split_mode') or 'none') != 'none':
            count = len([part for part in str(launch.get('tensor_split') or '').split(',') if part.strip()])
            if count > 1:
                return f'Automatic → split across {count} GPUs (main {main_name})'
            return f'Automatic → {main_name} (split across GPUs)'
        return f'Automatic → {main_name}'
    forced_name = devices.get(main_gpu, {}).get('display_name') or f'GPU {raw}'
    return forced_name


def get_gpu_devices_payload() -> dict[str, Any]:
    devices = query_gpu_devices()
    return {'success': True, 'gpus': devices, 'count': len(devices)}
