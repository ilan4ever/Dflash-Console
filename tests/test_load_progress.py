from core.load_progress import is_active_boot, parse_load_progress, stop_log_line


def test_active_boot_requires_stop_after_boot():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x ===',
        'load 50%',
    ]
    assert is_active_boot(lines) is True
    assert parse_load_progress(lines) == 50.0

    lines.append('=== stop 2026-01-01 12:05:00 ===')
    assert is_active_boot(lines) is False
    assert parse_load_progress(lines) is None


def test_new_boot_after_stop():
    lines = [
        '=== boot 2026-01-01 12:00:00 profile=x ===',
        '=== stop 2026-01-01 12:05:00 ===',
        '=== boot 2026-01-01 12:06:00 profile=x ===',
        'tensor offload 12.5%',
    ]
    assert is_active_boot(lines) is True
    assert parse_load_progress(lines) == 12.5


def test_stop_log_line_format():
    line = stop_log_line()
    assert line.startswith('=== stop ')
