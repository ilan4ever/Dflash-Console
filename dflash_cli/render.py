"""Human-friendly terminal output."""

from __future__ import annotations

import json
import shutil
import sys
from typing import Any, Iterable


def emit(text: str = '', *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(text + ('\n' if not text.endswith('\n') else ''))


def emit_json(payload: Any) -> int:
    emit(json.dumps(payload, indent=2, default=str))
    return 0


def fail(message: str, code: int = 1) -> int:
    emit(str(message).rstrip(), err=True)
    return code


def format_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return ''
    width = max(40, shutil.get_terminal_size((100, 20)).columns)
    headers = [title for title, _key in columns]
    cells = [[_cell(row.get(key)) for _title, key in columns] for row in rows]
    sizes = [len(header) for header in headers]
    for line in cells:
        for index, value in enumerate(line):
            sizes[index] = min(max(sizes[index], len(value)), 42)
    usable = width - (len(columns) - 1) * 2
    if sum(sizes) > usable:
        extra = sum(sizes) - usable
        longest = max(range(len(sizes)), key=lambda i: sizes[i])
        sizes[longest] = max(12, sizes[longest] - extra)
    head = '  '.join(header.ljust(sizes[i]) for i, header in enumerate(headers))
    lines = [head, '  '.join('-' * size for size in sizes)]
    for line in cells:
        lines.append('  '.join(_fit(value, sizes[i]).ljust(sizes[i]) for i, value in enumerate(line)))
    return '\n'.join(lines)


def _cell(value: Any) -> str:
    if value is None or value is False:
        return '-'
    if value is True:
        return 'yes'
    if isinstance(value, float):
        return f'{value:.2f}' if value < 10 else f'{value:.1f}'
    return str(value)


def _fit(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 1] + '…'


def yes_no(value: Any) -> str:
    return 'yes' if value else '-'


def join_nonempty(parts: Iterable[Any], sep: str = ' · ') -> str:
    return sep.join(str(part) for part in parts if part not in (None, '', []))
