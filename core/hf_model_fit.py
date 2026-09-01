"""Estimate whether Hugging Face catalog models fit the local machine."""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from typing import Any

from core.gpu_devices import VRAM_HEADROOM_GB
from core.memory_guardrails import _gpu_snapshot

_SHARD_RE = re.compile(
    r'^(?P<prefix>.+?)-(?P<part>\d{5})-of-(?P<total>\d{5})(?P<suffix>\.(?:gguf|safetensors|bin))$',
    re.IGNORECASE,
)
_KV_RESERVE_GB = 0.4
_GGUF_SIZE_MIN_GB = 0.05
_REPO_SIZE_MIN_GB = 0.01
_WEIGHT_SUFFIXES = (
    '.safetensors', '.bin', '.pt', '.pth', '.onnx', '.gguf',
    '.mlmodel', '.tflite', '.mlpackage',
)
_BUDGET_CACHE_SECONDS = 30.0
_budget_lock = threading.Lock()
_budget_cache: dict[str, Any] | None = None
_budget_cache_at = 0.0


def bytes_to_size_gb(size_bytes: int | float | None) -> float | None:
    try:
        nbytes = int(size_bytes or 0)
    except (TypeError, ValueError):
        return None
    if nbytes <= 0:
        return None
    gb = nbytes / (1024 ** 3)
    if gb < 0.1:
        return round(gb, 3)
    return round(gb, 2)


def _file_size_gb(row: dict[str, Any]) -> float | None:
    size_gb = row.get('size_gb')
    if isinstance(size_gb, (int, float)) and float(size_gb) > 0:
        return float(size_gb)
    return bytes_to_size_gb(row.get('size_bytes'))


def _is_weight_file(filename: str) -> bool:
    lower = str(filename or '').lower()
    return any(lower.endswith(suffix) for suffix in _WEIGHT_SUFFIXES)


def repo_weight_sizes_gb(files: list[dict[str, Any]] | None, *, has_gguf: bool) -> list[float]:
    """VRAM-oriented sizes: GGUF quants, or weight totals for full repos."""
    if not files:
        return []
    if has_gguf:
        return quant_sizes_gb(files)
    weights = [row for row in files if _is_weight_file(str(row.get('filename') or ''))]
    pool = weights or files
    max_bytes = 0
    total_bytes = 0
    for row in pool:
        try:
            size_bytes = int(row.get('size_bytes') or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes <= 0:
            parsed = _file_size_gb(row)
            if parsed:
                size_bytes = int(float(parsed) * (1024 ** 3))
        if size_bytes <= 0:
            continue
        total_bytes += size_bytes
        if size_bytes > max_bytes:
            max_bytes = size_bytes
    candidates = []
    if max_bytes > 0:
        gb = bytes_to_size_gb(max_bytes)
        if gb:
            candidates.append(gb)
    if total_bytes > 0:
        gb = bytes_to_size_gb(total_bytes)
        if gb:
            candidates.append(gb)
    return sorted({float(value) for value in candidates})


def repo_disk_size_gb(files: list[dict[str, Any]] | None, *, has_gguf: bool) -> float | None:
    """Approximate on-disk download total for catalog cards."""
    if not files:
        return None
    if has_gguf:
        sizes = quant_sizes_gb(files)
        return float(sizes[0]) if sizes else None
    total_bytes = 0
    for row in files:
        try:
            size_bytes = int(row.get('size_bytes') or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes <= 0:
            parsed = _file_size_gb(row)
            if parsed:
                size_bytes = int(float(parsed) * (1024 ** 3))
        if size_bytes > 0:
            total_bytes += size_bytes
    return bytes_to_size_gb(total_bytes)


def _size_fallback_min_gb(model: dict[str, Any]) -> float:
    """Minimum parsed size_gb before we trust a search-row estimate."""
    if model.get('has_gguf') or model.get('accelerator_only'):
        return _GGUF_SIZE_MIN_GB
    return _REPO_SIZE_MIN_GB


def machine_fit_budget_gb(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return VRAM budget used for catalog fit heuristics."""
    global _budget_cache, _budget_cache_at
    from core.config import load_config

    config = cfg or load_config()
    now = time.time()
    with _budget_lock:
        if _budget_cache and now - _budget_cache_at < _BUDGET_CACHE_SECONDS:
            return dict(_budget_cache)

    devices = _gpu_snapshot(config)
    per_gpu_usable: list[float] = []
    total_gb = 0.0
    free_gb = 0.0
    for item in devices:
        gpu_total = float(item.get('vram_gb') or 0.0)
        if gpu_total <= 0:
            continue
        gpu_free = float(item.get('vram_free_gb') or 0.0)
        total_gb += gpu_total
        free_gb += gpu_free
        per_gpu_usable.append(max(0.0, gpu_total - VRAM_HEADROOM_GB - _KV_RESERVE_GB))
    best_single = round(max(per_gpu_usable), 2) if per_gpu_usable else 0.0
    multi_usable = round(max(0.0, total_gb - VRAM_HEADROOM_GB - _KV_RESERVE_GB), 2)
    result = {
        'vram_total_gb': round(total_gb, 2),
        'vram_free_gb': round(free_gb, 2),
        # Single-model loads usually need one GPU — use the largest card, not a sum.
        'fits_budget_gb': best_single,
        'fits_budget_multi_gpu_gb': multi_usable,
        'gpu_count': len(per_gpu_usable),
        'headroom_gb': VRAM_HEADROOM_GB,
        'kv_reserve_gb': _KV_RESERVE_GB,
    }
    with _budget_lock:
        _budget_cache = dict(result)
        _budget_cache_at = now
    return dict(result)


def _shard_group_key(filename: str) -> str:
    normalized = str(filename or '').replace('\\', '/')
    base = normalized.split('/')[-1]
    match = _SHARD_RE.match(base)
    if not match:
        return normalized
    folder = normalized.rsplit('/', 1)[0] if '/' in normalized else ''
    prefix = f'{folder}/' if folder else ''
    return f'{prefix}{match.group("prefix")}|{match.group("total")}'


def preferred_gguf_fit_size_gb(
    model: dict[str, Any],
    gguf_files: list[dict[str, Any]] | None,
) -> float | None:
    """VRAM-oriented size for fit checks — matches the catalog card disk size."""
    size_gb = model.get('size_gb')
    if isinstance(size_gb, (int, float)) and float(size_gb) > 0:
        return round(float(size_gb), 2)
    if not gguf_files:
        return None
    from core.huggingface import _size_from_preferred_quant

    preferred_gb, _label = _size_from_preferred_quant(gguf_files)
    if preferred_gb:
        return round(float(preferred_gb), 2)
    sizes = quant_sizes_gb(gguf_files)
    return sizes[0] if sizes else None


def quant_sizes_gb(gguf_files: list[dict[str, Any]] | None) -> list[float]:
    """Return sorted GGUF quant totals, summing multi-shard groups."""
    groups: dict[str, int] = defaultdict(int)
    for row in gguf_files or []:
        if not isinstance(row, dict):
            continue
        filename = str(row.get('filename') or '').strip()
        if not filename.lower().endswith('.gguf'):
            continue
        try:
            size_bytes = int(row.get('size_bytes') or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes <= 0:
            continue
        groups[_shard_group_key(filename)] += size_bytes
    return sorted(round(total / (1024 ** 3), 2) for total in groups.values())


def assess_hf_model_fit(
    model: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    gguf_files: list[dict[str, Any]] | None = None,
    download_files: list[dict[str, Any]] | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Annotate a catalog row with machine-fit metadata."""
    fit_budget = budget or machine_fit_budget_gb(cfg)
    budget_gb = float(fit_budget['fits_budget_gb'] or 0.0)

    has_gguf = bool(model.get('has_gguf')) or bool(gguf_files)
    uncertain = False
    sizes: list[float] = []
    if has_gguf:
        fit_size = preferred_gguf_fit_size_gb(model, gguf_files)
        if fit_size is not None:
            sizes = [fit_size]
        elif gguf_files:
            all_sizes = quant_sizes_gb(gguf_files)
            if all_sizes:
                sizes = [all_sizes[0]]
            else:
                uncertain = True
        else:
            uncertain = True
    else:
        display_gb = model.get('size_gb')
        try:
            parsed_display = float(display_gb)
        except (TypeError, ValueError):
            parsed_display = 0.0
        if parsed_display >= _size_fallback_min_gb(model):
            sizes = [round(parsed_display, 2)]
        else:
            files = download_files if isinstance(download_files, list) else model.get('download_files')
            if isinstance(files, list) and files:
                sizes = repo_weight_sizes_gb(files, has_gguf=False)
            if not sizes:
                min_gb = _size_fallback_min_gb(model)
                if parsed_display >= min_gb:
                    sizes = [round(parsed_display, 2)]
                else:
                    uncertain = True

    fitting = [size for size in sizes if size <= budget_gb]
    return {
        **fit_budget,
        'fits_machine': bool(fitting),
        'fits_machine_uncertain': uncertain,
        'fits_machine_reason': 'uncertain' if uncertain else ('fits' if fitting else 'too_large'),
        'smallest_quant_gb': min(sizes) if sizes else None,
        'best_fit_quant_gb': max(fitting) if fitting else None,
        'quant_options_gb': sizes,
    }


def annotate_hf_models_fit(
    models: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
    category: str = 'supported',
) -> list[dict[str, Any]]:
    """Add fits_machine metadata to HF search rows (uses cached detail when present)."""
    from core.config import load_config
    from core.hf_catalog_cache import get_cached_detail

    config = cfg or load_config()
    budget = machine_fit_budget_gb(config)
    for row in models:
        if not isinstance(row, dict):
            continue
        repo_id = str(row.get('id') or '').strip()
        cat_key = str(row.get('category') or category)
        gguf_files = row.get('gguf_files') if isinstance(row.get('gguf_files'), list) else None
        download_files = row.get('download_files') if isinstance(row.get('download_files'), list) else None
        if not gguf_files and repo_id:
            cached = get_cached_detail(repo_id=repo_id, category=cat_key)
            cached_model = (cached or {}).get('payload', {}).get('model')
            if isinstance(cached_model, dict):
                files = cached_model.get('gguf_files')
                if isinstance(files, list) and files:
                    gguf_files = files
                dl = cached_model.get('download_files')
                if isinstance(dl, list) and dl:
                    download_files = dl
        row.update(
            assess_hf_model_fit(
                row,
                cfg=config,
                gguf_files=gguf_files,
                download_files=download_files,
                budget=budget,
            ),
        )
        label = str(row.get('size_label') or '').strip()
        if label in ('0 GB', '0.0 GB'):
            label = ''
        if (not isinstance(row.get('size_gb'), (int, float)) or float(row.get('size_gb') or 0) <= 0) and (not label or label == '—'):
            disk_gb = None
            if isinstance(download_files, list) and download_files:
                from core.hf_model_fit import repo_disk_size_gb

                disk_gb = repo_disk_size_gb(download_files, has_gguf=bool(row.get('has_gguf')))
            if not disk_gb:
                smallest = row.get('smallest_quant_gb')
                if isinstance(smallest, (int, float)) and smallest > 0:
                    disk_gb = float(smallest)
            if isinstance(disk_gb, (int, float)) and disk_gb > 0:
                row['size_gb'] = round(float(disk_gb), 2)
                row['size_label'] = f'{float(disk_gb):g} GB'
    return models
