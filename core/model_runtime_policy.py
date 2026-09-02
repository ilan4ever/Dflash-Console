"""Runtime selection policy for models that exceed local resource limits."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

LARGE_HF_MODEL_GB = 48.0
_VIRTUAL_MEMORY_ERROR_RE = re.compile(
    r'(?:1455|paging file|pagefile|virtual memory|commit limit)',
    re.IGNORECASE,
)


def model_disk_size_gb(model: dict[str, Any] | None) -> float:
    if not isinstance(model, dict):
        return 0.0
    try:
        value = model.get('size_gb')
        if value is not None:
            return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        path = Path(str(model.get('path') or '')).expanduser()
        if path.is_file():
            return path.stat().st_size / (1024 ** 3)
        if path.is_dir():
            return sum(
                item.stat().st_size
                for item in path.rglob('*')
                if item.is_file() and item.suffix.lower() in {'.safetensors', '.bin', '.pt', '.pth'}
            ) / (1024 ** 3)
    except (OSError, ValueError):
        pass
    return 0.0


def requires_freetoken(model: dict[str, Any] | None) -> bool:
    """Whether a local HF checkpoint should be loaded through FreeToken."""
    if not isinstance(model, dict):
        return False
    path = Path(str(model.get('path') or '')).expanduser()
    is_hf_folder = str(model.get('kind') or '').lower() == 'dir' or path.is_dir()
    engines = {str(item).strip().lower() for item in (model.get('engines') or [])}
    return is_hf_folder and 'freetoken' in engines and model_disk_size_gb(model) >= LARGE_HF_MODEL_GB


def runtime_load_block(
    model: dict[str, Any] | None,
    runtime_id: str,
) -> dict[str, Any] | None:
    """Return an actionable warning when an oversized model uses another engine."""
    runtime = str(runtime_id or '').strip().lower()
    if not requires_freetoken(model) or runtime in {'', 'freetoken'}:
        return None
    size_gb = model_disk_size_gb(model)
    label = str((model or {}).get('label') or (model or {}).get('filename') or 'This model').strip()
    return {
        'code': 'freetoken-required',
        'runtime_id': runtime,
        'recommended_runtime': 'freetoken',
        'model': label,
        'size_gb': round(size_gb, 2),
        'message': (
            f'{label} is about {size_gb:.0f} GB and cannot be loaded reliably with '
            f'{runtime}. Use FreeToken (WSL) for this large model; it is the supported '
            'low-resource engine. FreeToken still requires enough Windows virtual memory '
            '(set the paging file to System managed or approximately 200 GB).'
        ),
    }


def explain_freetoken_load_error(result: dict[str, Any] | None) -> str | None:
    """Turn the common Windows/WSL allocation failure into setup guidance."""
    if not isinstance(result, dict):
        return None
    detail = ' '.join(
        str(result.get(key) or '')
        for key in ('error', 'detail', 'message')
    )
    if not _VIRTUAL_MEMORY_ERROR_RE.search(detail):
        return None
    return (
        'FreeToken could not reserve enough virtual memory for this model. '
        'Increase the Windows paging file to System managed or approximately 200 GB, '
        'then restart WSL/Windows and load the model again. This is a Windows memory '
        'limit, not a model-download failure.'
    )
