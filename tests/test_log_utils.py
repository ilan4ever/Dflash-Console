from __future__ import annotations

from pathlib import Path

from core.log_utils import read_tail_lines, rotate_log


def test_read_tail_lines_is_bounded(tmp_path: Path):
    path = tmp_path / 'engine.log'
    path.write_text(''.join(f'line-{index}\n' for index in range(100)), encoding='utf-8')

    lines, truncated = read_tail_lines(path, max_lines=3, max_bytes=32)

    assert lines == ['line-97', 'line-98', 'line-99']
    assert truncated is True


def test_rotate_log_keeps_bounded_backups(tmp_path: Path):
    path = tmp_path / 'engine.log'
    path.write_text('x' * 32, encoding='utf-8')

    rotate_log(path, max_bytes=16, backups=2)

    assert not path.exists()
    assert (tmp_path / 'engine.log.1').read_text(encoding='utf-8') == 'x' * 32
