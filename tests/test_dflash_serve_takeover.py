from __future__ import annotations

from pathlib import Path

import pytest

from dflash_cli import __version__
from dflash_cli.server_takeover import console_roots_match, console_versions_match


def test_console_roots_match_process_root(tmp_path: Path):
    target = tmp_path / 'data'
    target.mkdir()
    health = {'process_root': str(target), 'console_root': str(tmp_path / 'other')}
    assert console_roots_match(health, target) is True


def test_console_versions_match():
    health = {'version': __version__}
    assert console_versions_match(health, __version__) is True
    assert console_versions_match(health, '0.0.0') is False


def test_cmd_serve_skips_when_same_root_and_version(monkeypatch):
    from dflash_cli.commands import cmd_serve

    root = Path('C:/data/console')
    health = {'process_root': str(root), 'version': __version__}

    monkeypatch.setattr('dflash_cli.commands.ensure_console_data_root', lambda: root)
    monkeypatch.setattr('dflash_cli.commands.console_health', lambda port: health)
    monkeypatch.setattr('dflash_cli.commands.console_roots_match', lambda h, r: True)
    monkeypatch.setattr('dflash_cli.commands.console_versions_match', lambda h, v: True)
    called = {'stop': False}
    monkeypatch.setattr(
        'dflash_cli.commands.stop_console_on_port',
        lambda port: called.__setitem__('stop', True) or True,
    )

    class Args:
        port = 8900

    assert cmd_serve(Args()) == 0
    assert called['stop'] is False


def test_is_console_listener_accepts_python_when_command_line_blank(monkeypatch):
    from dflash_cli.server_takeover import _is_console_listener

    monkeypatch.setattr('dflash_cli.server_takeover._process_command_line', lambda pid: '')
    monkeypatch.setattr('dflash_cli.server_takeover._process_name', lambda pid: 'python.exe')
    monkeypatch.setattr('dflash_cli.server_takeover.sys.platform', 'win32')
    assert _is_console_listener(44668) is True


def test_is_console_listener_rejects_non_python_without_uvicorn(monkeypatch):
    from dflash_cli.server_takeover import _is_console_listener

    monkeypatch.setattr('dflash_cli.server_takeover._process_command_line', lambda pid: '')
    monkeypatch.setattr('dflash_cli.server_takeover._process_name', lambda pid: 'node.exe')
    monkeypatch.setattr('dflash_cli.server_takeover.sys.platform', 'win32')
    assert _is_console_listener(1234) is False


def test_cmd_serve_stops_foreign_console(monkeypatch):
    from dflash_cli.commands import cmd_serve

    root = Path('C:/data/pip-console')
    health = {'process_root': 'C:/dev/Dflash-Console', 'version': '0.0.1'}
    calls = {'stop': 0, 'serve': 0}

    monkeypatch.setattr('dflash_cli.commands.ensure_console_data_root', lambda: root)
    monkeypatch.setattr('dflash_cli.commands.console_health', lambda port: health)
    monkeypatch.setattr('dflash_cli.commands.console_roots_match', lambda h, r: False)
    monkeypatch.setattr('dflash_cli.commands.console_versions_match', lambda h, v: False)
    monkeypatch.setattr(
        'dflash_cli.commands.stop_console_on_port',
        lambda port: calls.__setitem__('stop', calls['stop'] + 1) or True,
    )
    monkeypatch.setattr(
        'dflash_cli.commands.subprocess.call',
        lambda *a, **k: calls.__setitem__('serve', 1) or 0,
    )

    class Args:
        port = 8900

    assert cmd_serve(Args()) == 0
    assert calls['stop'] == 1
    assert calls['serve'] == 1
