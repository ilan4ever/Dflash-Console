"""Bounded log reads and small rolling log rotation helpers."""

from __future__ import annotations

import os
from pathlib import Path


def read_tail_lines(
    path: Path,
    *,
    max_lines: int = 200,
    max_bytes: int = 1_048_576,
) -> tuple[list[str], bool]:
    limit_lines = max(1, int(max_lines or 200))
    limit_bytes = max(1, int(max_bytes or 1_048_576))
    if not path.is_file():
        return [], False
    try:
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - limit_bytes)
            handle.seek(start)
            if start:
                handle.readline()
            data = handle.read(limit_bytes + 1)
    except OSError:
        return [], False
    truncated = start > 0 or len(data) > limit_bytes
    text = data[:limit_bytes].decode('utf-8', errors='replace')
    return text.splitlines()[-limit_lines:], truncated


def rotate_log(path: Path, *, max_bytes: int = 5 * 1024 * 1024, backups: int = 3) -> None:
    try:
        if not path.is_file() or path.stat().st_size < max(1, int(max_bytes)):
            return
        count = max(1, int(backups))
        oldest = path.with_name(f'{path.name}.{count}')
        if oldest.exists():
            oldest.unlink()
        for index in range(count - 1, 0, -1):
            source = path.with_name(f'{path.name}.{index}')
            if source.exists():
                source.replace(path.with_name(f'{path.name}.{index + 1}'))
        path.replace(path.with_name(f'{path.name}.1'))
    except OSError:
        return
