"""Detect MTP / llama.cpp draft sidecars in Hugging Face catalog rows."""

from __future__ import annotations

import re
from typing import Any

_DRAFT_NAME_RE = re.compile(
    r'(?:^|[._-])(?:draft|fastmtp|mtp-head|mtp-draft)(?:[._-]|\.gguf$)',
    re.I,
)
_PARAM_B_RE = re.compile(r'(\d+(?:\.\d+)?)\s*b\b', re.I)


def _repo_slug(row: dict[str, Any]) -> str:
    repo_id = str(row.get('id') or row.get('label') or '').strip()
    return repo_id.split('/')[-1].lower() if '/' in repo_id else repo_id.lower()


def _param_billions(slug: str) -> float | None:
    matches = [float(value) for value in _PARAM_B_RE.findall(slug or '')]
    if not matches:
        return None
    return max(matches)


def _display_size_gb(row: dict[str, Any]) -> float | None:
    size_gb = row.get('size_gb')
    if isinstance(size_gb, (int, float)) and float(size_gb) > 0:
        return float(size_gb)
    return None


def _bytes_implied_gb(row: dict[str, Any]) -> float | None:
    try:
        size_bytes = int(row.get('size_bytes') or 0)
    except (TypeError, ValueError):
        return None
    if size_bytes <= 0:
        return None
    return size_bytes / (1024 ** 3)


def catalog_draft_primary_row(
    row: dict[str, Any],
    *,
    gguf_files: list[dict[str, Any]] | None = None,
) -> bool:
    """True when the catalog row is dominated by an MTP/draft sidecar, not a full target GGUF."""
    if not isinstance(row, dict):
        return False
    if row.get('accelerator_only'):
        return False
    if not (row.get('has_gguf') or gguf_files):
        return False

    slug = _repo_slug(row)
    params_b = _param_billions(slug)
    display_gb = _display_size_gb(row)
    bytes_gb = _bytes_implied_gb(row)

    from core.hf_local_match import is_auxiliary_gguf_filename
    from core.hf_model_fit import quant_sizes_gb

    files = gguf_files if isinstance(gguf_files, list) else row.get('gguf_files')
    non_aux = [
        item for item in (files or [])
        if isinstance(item, dict)
        and not is_auxiliary_gguf_filename(str(item.get('filename') or ''))
    ]
    target_sizes = quant_sizes_gb(non_aux) if non_aux else []
    max_target_gb = max(target_sizes) if target_sizes else None

    if params_b and params_b >= 7:
        if display_gb is not None and display_gb < 6:
            return True
        if max_target_gb is not None and max_target_gb < 6:
            return True
        if (
            display_gb is not None
            and display_gb < 6
            and bytes_gb is not None
            and bytes_gb >= 8
        ):
            return True

    if non_aux and all(
        _DRAFT_NAME_RE.search(str(item.get('filename') or ''))
        for item in non_aux
    ):
        return True

    if len(non_aux) == 1:
        name = str(non_aux[0].get('filename') or '').lower()
        if _DRAFT_NAME_RE.search(name):
            return True
        if 'fastmtp' in name and (max_target_gb or display_gb or 0) < 6:
            return True

    return False
