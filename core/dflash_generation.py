"""DFlash 1 vs DFlash 2 accelerator generation helpers."""

from __future__ import annotations

import re
from pathlib import Path

_DFLASH2_RE = re.compile(r'dflash[-_.]?2|dflash2', re.I)


def infer_dflash_generation(path: str | Path | None) -> str:
    """Return ``dflash2`` or ``dflash1`` from an accelerator filename."""
    if not path:
        return 'dflash1'
    text = Path(path).name.lower()
    if _DFLASH2_RE.search(text):
        return 'dflash2'
    return 'dflash1'


def dflash_generation_label(generation: str | None) -> str:
    gen = str(generation or 'dflash1').strip().lower()
    return 'DFlash 2' if gen == 'dflash2' else 'DFlash 1'


def normalize_dflash_generation(value: str | None, *, default: str = 'dflash1') -> str:
    gen = str(value or default).strip().lower()
    if gen in {'dflash2', '2', 'v2'}:
        return 'dflash2'
    if gen in {'auto', 'any', 'all'}:
        return 'auto'
    return 'dflash1'


def hf_category_for_generation(generation: str | None) -> str:
    return 'dflash2' if normalize_dflash_generation(generation) == 'dflash2' else 'dflash'


def spec_draft_n_max(
    *,
    draft_path: str | Path | None = None,
    profile: str = '',
    generation: str | None = None,
) -> int:
    if str(profile or '').strip() == 'bonsai-spec':
        return 4
    gen = generation or infer_dflash_generation(draft_path)
    return 7 if gen == 'dflash2' else 8


def repo_dflash_generation(*parts: str) -> str:
    text = ' '.join(str(part or '') for part in parts).lower()
    if _DFLASH2_RE.search(text):
        return 'dflash2'
    return 'dflash1'
