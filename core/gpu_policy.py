"""GPU performance mode — desktop VRAM headroom and optional unload-on-load."""

from __future__ import annotations

from typing import Any

from core.config import normalize_hardware_settings

GPU_PERFORMANCE_MODES = frozenset({'performance', 'balanced', 'power'})

_MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    'performance': {
        'desktop_vram_reserve_gb': 8.0,
        'stop_others_on_load': True,
    },
    'balanced': {
        'desktop_vram_reserve_gb': 6.0,
        'stop_others_on_load': False,
    },
    'power': {
        'desktop_vram_reserve_gb': 4.0,
        'stop_others_on_load': False,
    },
}


def normalize_gpu_performance_mode(raw: Any) -> str:
    mode = str(raw or 'balanced').strip().lower()
    if mode not in GPU_PERFORMANCE_MODES:
        return 'balanced'
    return mode


def gpu_performance_mode(cfg: dict[str, Any] | None) -> str:
    hw = normalize_hardware_settings((cfg or {}).get('hardware_settings'))
    return normalize_gpu_performance_mode(hw.get('gpu_performance_mode'))


def gpu_policy_for_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Resolved desktop VRAM reserve for the active performance mode."""
    config = cfg or {}
    hw = normalize_hardware_settings(config.get('hardware_settings'))
    mode = normalize_gpu_performance_mode(hw.get('gpu_performance_mode'))
    defaults = dict(_MODE_DEFAULTS[mode])
    try:
        reserve_override = float(hw.get('desktop_vram_reserve_gb') or 0.0)
    except (TypeError, ValueError):
        reserve_override = 0.0
    if reserve_override > 0:
        defaults['desktop_vram_reserve_gb'] = reserve_override
    defaults['mode'] = mode
    return defaults


def should_stop_others_on_load(cfg: dict[str, Any] | None) -> bool:
    """Honor explicit config; otherwise follow the active performance mode."""
    if cfg is None:
        return False
    explicit = cfg.get('runtime_stop_others_on_load')
    if explicit is True:
        return True
    if explicit is False:
        return False
    return bool(gpu_policy_for_config(cfg).get('stop_others_on_load'))
