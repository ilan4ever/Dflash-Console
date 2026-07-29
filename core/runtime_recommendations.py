"""Hardware-aware runtime setting recommendations for the inspector."""

from __future__ import annotations

import os
from typing import Any

from core.config import load_config, normalize_hardware_settings, normalize_inference_settings, normalize_load_settings
from core.memory_guardrails import _enabled_gpu_indices, _vram_budget
from core.model_stack import resolve_model_stack
from core.system_stats import get_cpu_info_payload, get_system_stats_payload

CONTEXT_LADDER = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144]
MAX_TOKEN_LADDER = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768]
BATCH_LADDER = [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
SPEC_PROFILES = frozenset({'gemma-chat', 'gemma-12-dflash', 'qwen-dflash', 'bonsai-spec'})

PROFILE_CTX_MAX = {
    'gemma-chat': 262144,
    'gemma-ar': 262144,
    'gemma-12-dflash': 262144,
    'qwen-dflash': 32768,
    'qwen-ar': 32768,
    'bonsai': 8192,
    'bonsai-spec': 16384,
}


def _snap_ladder(value: int, ladder: list[int], *, min_v: int, max_v: int) -> int:
    choices = [v for v in ladder if min_v <= v <= max_v]
    if not choices:
        return min_v
    eligible = [v for v in choices if v <= value]
    return max(eligible) if eligible else choices[0]


def _gpu_layer_steps(max_layers: int) -> list[int]:
    cap = max(0, int(max_layers or 128))
    steps = [0]
    for value in range(8, 96, 8):
        steps.append(value)
    if cap >= 99:
        steps.append(99)
    for value in range(104, cap + 1, 8):
        steps.append(value)
    if cap >= 128 and 128 not in steps:
        steps.append(128)
    return sorted({v for v in steps if v <= cap})


def _snap_gpu_layers(value: int, max_layers: int) -> int:
    steps = _gpu_layer_steps(max_layers)
    if not steps:
        return 0
    eligible = [v for v in steps if v <= value]
    return max(eligible) if eligible else steps[0]


def _layer_on_gpu_fraction(gpu_layers: int) -> float:
    if gpu_layers <= 0:
        return 0.05
    if gpu_layers >= 99:
        return 1.0
    return min(1.0, max(0.08, gpu_layers / 99.0))


def _kv_cache_gb(context: int) -> float:
    return round(max(0.0, (max(2048, context) / 8192) * 0.4), 2)


def _estimate_vram_gb(*, weight_gb: float, context: int, gpu_layers: int) -> float:
    on_gpu = _layer_on_gpu_fraction(gpu_layers)
    gpu_weights = weight_gb * on_gpu
    cpu_weights = weight_gb * (1.0 - on_gpu) * 0.25
    return round(gpu_weights + _kv_cache_gb(context) + cpu_weights, 2)


def _stack_weight_gb(server: dict[str, Any], cfg: dict[str, Any]) -> tuple[float, float, bool]:
    stack = resolve_model_stack(server, cfg=cfg)
    target_gb = 0.0
    draft_gb = 0.0
    for row in stack:
        size = row.get('size_gb')
        if size is None and row.get('path'):
            from pathlib import Path

            try:
                size = round(Path(str(row['path'])).stat().st_size / (1024 ** 3), 2)
            except OSError:
                size = 0.0
        size_f = float(size or 0.0)
        role = str(row.get('role') or '')
        if role == 'target':
            target_gb += size_f
        elif role.startswith('draft'):
            draft_gb += size_f
    profile = str(server.get('profile') or '')
    speculative = profile in SPEC_PROFILES and draft_gb > 0
    total = target_gb + (draft_gb if speculative else 0.0)
    if total <= 0:
        total = float(server.get('size_gb') or 12.0)
    return round(total, 2), round(target_gb or total, 2), speculative


def _cpu_thread_recommendation(cfg: dict[str, Any]) -> tuple[int, str]:
    logical = os.cpu_count() or 8
    stats = get_system_stats_payload()
    cpu_pct = stats.get('cpu_percent')
    base = max(4, min(logical - 1, 16))
    if isinstance(cpu_pct, int) and cpu_pct >= 85:
        base = max(4, base - 2)
    reason = (
        f'Uses {base} of {logical} logical cores — leaves headroom for the OS while keeping prompt processing fast.'
    )
    if isinstance(cpu_pct, int) and cpu_pct >= 85:
        reason = (
            f'CPU is already at {cpu_pct}% load, so {base} threads avoids oversubscribing your machine.'
        )
    return base, reason


def _batch_recommendations(free_gb: float, gpu_layers: int) -> tuple[int, int, str]:
    tight = free_gb < 8 or gpu_layers < 32
    moderate = free_gb < 16
    if tight:
        return 256, 128, 'Smaller batches keep VRAM stable when GPU memory is tight or layers are mostly on CPU.'
    if moderate:
        return 1024, 256, f'With ~{free_gb:.0f} GB free VRAM, mid-size batches balance speed and memory headroom.'
    return 2048, 512, f'With ~{free_gb:.0f} GB free VRAM, larger batches improve token throughput on GPU.'


def _sampling_recommendations(profile: str) -> tuple[dict[str, Any], dict[str, str]]:
    values = normalize_inference_settings({})
    reasons = {
        'temperature': '0.7 is a balanced default for chat — creative but still coherent.',
        'top_p': '0.9 nucleus sampling trims unlikely tokens without making output too flat.',
        'top_k': '40 is a common default that works well across instruct models.',
        'repeat_penalty': '1.1 gently reduces repetitive phrasing in long replies.',
        'max_tokens': 'Caps each response so generation stops predictably during testing.',
    }
    if profile in {'gemma-ar', 'qwen-ar', 'gemma-12-ar'}:
        values['temperature'] = 0.6
        reasons['temperature'] = 'Slightly lower temperature suits autoregressive / deterministic profiles.'
    return values, reasons


def _hardware_summary(free_gb: float, total_gb: float, gpu_count: int, cfg: dict[str, Any]) -> str:
    if gpu_count <= 0:
        return 'No enabled NVIDIA GPU — recommendations assume CPU-heavy loading.'
    indices = _enabled_gpu_indices(cfg)
    devices = []
    stats = get_system_stats_payload()
    for gpu in stats.get('gpus') or []:
        if int(gpu.get('index', -1)) in indices:
            name = gpu.get('display_name') or gpu.get('name') or f'GPU {gpu.get("index")}'
            devices.append(str(name))
    device_text = ', '.join(devices[:2]) + (f' +{len(devices) - 2} more' if len(devices) > 2 else '')
    if not device_text:
        device_text = f'{gpu_count} GPU{"s" if gpu_count != 1 else ""}'
    return f'Tuned for {device_text} (~{free_gb:.0f} GB free of {total_gb:.0f} GB VRAM).'


def build_runtime_recommendations(
    *,
    server: dict[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    profile = str(server.get('profile') or '')
    context_max = int(
        server.get('context_max')
        or PROFILE_CTX_MAX.get(profile)
        or 131072
    )
    gpu_layers_max = int(server.get('gpu_layers_max') or 128)
    weight_gb, target_gb, speculative = _stack_weight_gb(server, config)
    free_gb, total_gb, gpu_count = _vram_budget(config)

    fields: dict[str, dict[str, Any]] = {}

    if gpu_count <= 0:
        gpu_layers = 0
        gpu_reason = 'No enabled NVIDIA GPU detected — keep layers on CPU to avoid failed loads.'
        flash = False
        flash_reason = 'Flash attention needs a GPU; disabled for CPU-only loading.'
    else:
        flash = True
        flash_reason = 'Flash attention lowers VRAM use and speeds up attention on modern NVIDIA GPUs.'
        gpu_layers = 99
        gpu_reason = (
            f'Full offload fits: the checkpoint stack (~{weight_gb:.1f} GB) leaves room in ~{free_gb:.0f} GB free VRAM.'
        )
        if weight_gb > free_gb * 0.52:
            best_layers = 0
            best_context = 8192
            for candidate in reversed(_gpu_layer_steps(gpu_layers_max)):
                for ctx_try in reversed([v for v in CONTEXT_LADDER if v <= context_max]):
                    est = _estimate_vram_gb(weight_gb=weight_gb, context=ctx_try, gpu_layers=candidate)
                    if est <= free_gb * 0.92:
                        if candidate > best_layers or (candidate == best_layers and ctx_try > best_context):
                            best_layers = candidate
                            best_context = ctx_try
                        break
            gpu_layers = best_layers
            if gpu_layers >= 99:
                gpu_reason = (
                    f'All layers on GPU — stack ~{weight_gb:.1f} GB with headroom in ~{free_gb:.0f} GB free VRAM.'
                )
            elif gpu_layers > 0:
                gpu_reason = (
                    f'Partial offload ({gpu_layers} layers): ~{weight_gb:.1f} GB stack exceeds comfortable full-GPU '
                    f'fit in ~{free_gb:.0f} GB free. Remaining layers stay on CPU.'
                )
            else:
                gpu_reason = (
                    f'VRAM is tight (~{free_gb:.0f} GB free for ~{weight_gb:.1f} GB weights) — CPU offload avoids OOM.'
                )

    gpu_layers = _snap_gpu_layers(gpu_layers, gpu_layers_max)

    context_target = context_max
    if gpu_count > 0:
        headroom = max(0.0, free_gb - _estimate_vram_gb(weight_gb=weight_gb, context=8192, gpu_layers=gpu_layers) + _kv_cache_gb(8192))
        max_ctx_from_vram = int((headroom / 0.4) * 8192) if headroom > 0 else 8192
        context_target = min(context_max, max(8192, max_ctx_from_vram))
        if speculative:
            context_target = min(context_target, 65536)
    else:
        context_target = min(context_max, 32768)

    context_size = _snap_ladder(context_target, CONTEXT_LADDER, min_v=2048, max_v=context_max)
    ctx_reason = (
        f'Model allows up to {context_max:,} tokens. '
        f'At {context_size:,} tokens the KV cache uses ~{_kv_cache_gb(context_size):.1f} GB'
    )
    if context_size < context_max:
        ctx_reason += (
            f', leaving VRAM for ~{weight_gb:.1f} GB weights on your ~{free_gb:.0f} GB free GPU memory.'
        )
    else:
        ctx_reason += f' — your GPU headroom supports the model maximum.'

    cpu_threads, cpu_reason = _cpu_thread_recommendation(config)
    eval_batch, physical_batch, batch_reason = _batch_recommendations(free_gb, gpu_layers)
    eval_batch = _snap_ladder(eval_batch, BATCH_LADDER, min_v=32, max_v=8192)
    physical_batch = _snap_ladder(physical_batch, BATCH_LADDER, min_v=32, max_v=8192)

    infer_defaults, infer_reasons = _sampling_recommendations(profile)
    max_tokens_cap = min(context_size, 32768)
    max_tokens = _snap_ladder(min(4096, max(1024, context_size // 8)), MAX_TOKEN_LADDER, min_v=256, max_v=max_tokens_cap)
    infer_defaults['max_tokens'] = max_tokens
    infer_reasons['max_tokens'] = (
        f'{max_tokens:,} tokens per reply — about {max(1, context_size // max_tokens)}× the context window for long answers without hogging VRAM.'
    )

    load_settings = normalize_load_settings({
        'gpu_layers': gpu_layers,
        'cpu_threads': cpu_threads,
        'eval_batch_size': eval_batch,
        'physical_batch_size': physical_batch,
        'flash_attention': flash,
    })
    inference_settings = normalize_inference_settings(infer_defaults)

    fields['context_size'] = {
        'value': context_size,
        'hint': f'Model supports up to {context_max:,} tokens (recommended: {context_size:,}).',
        'reason': ctx_reason,
    }
    fields['max_tokens'] = {
        'value': max_tokens,
        'hint': f'Max output tokens per request (recommended: {max_tokens:,}).',
        'reason': infer_reasons['max_tokens'],
    }
    fields['gpu_layers'] = {
        'value': gpu_layers,
        'hint': f'Layers on GPU —ngl (recommended: {gpu_layers}{" = all layers" if gpu_layers >= 99 else ""}).',
        'reason': gpu_reason,
    }
    fields['flash_attention'] = {
        'value': flash,
        'hint': f'Flash attention (recommended: {"on" if flash else "off"}).',
        'reason': flash_reason,
    }
    fields['cpu_threads'] = {
        'value': cpu_threads,
        'hint': f'Thread pool (recommended: {cpu_threads}).',
        'reason': cpu_reason,
    }
    fields['eval_batch_size'] = {
        'value': eval_batch,
        'hint': f'Eval batch (recommended: {eval_batch}).',
        'reason': batch_reason,
    }
    fields['physical_batch_size'] = {
        'value': physical_batch,
        'hint': f'Physical batch (recommended: {physical_batch}).',
        'reason': batch_reason,
    }
    for key in ('temperature', 'top_p', 'top_k', 'repeat_penalty'):
        fields[key] = {
            'value': inference_settings[key],
            'hint': f'Recommended: {inference_settings[key]}.',
            'reason': infer_reasons[key],
        }

    return {
        'success': True,
        'summary': _hardware_summary(free_gb, total_gb, gpu_count, config),
        'hardware': {
            'free_vram_gb': free_gb,
            'total_vram_gb': total_gb,
            'gpu_count': gpu_count,
            'weight_gb': weight_gb,
            'target_gb': target_gb,
            'speculative': speculative,
        },
        'values': {
            'context_size': context_size,
            'load_settings': load_settings,
            'inference_settings': inference_settings,
        },
        'fields': fields,
    }


def get_runtime_recommendations_payload(
    *,
    server_id: str | None = None,
    profile: str | None = None,
    size_gb: float | None = None,
    context_max: int | None = None,
    gpu_layers_max: int | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = cfg or load_config()
    server: dict[str, Any] = {}
    if server_id:
        from core.config import get_server

        found = get_server(config, server_id)
        if not found:
            return {'success': False, 'error': f'Unknown server {server_id}'}
        server = dict(found)
    if profile:
        server['profile'] = profile
    if size_gb is not None:
        server['size_gb'] = size_gb
    if context_max is not None:
        server['context_max'] = context_max
    if gpu_layers_max is not None:
        server['gpu_layers_max'] = gpu_layers_max
    if not server.get('profile') and not server.get('model_id'):
        server.setdefault('profile', 'gemma-chat')
        server.setdefault('model_id', 'browse')
    return build_runtime_recommendations(server=server, cfg=config)
