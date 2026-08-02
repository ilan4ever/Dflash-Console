from __future__ import annotations

import json

import core.server_boot as server_boot


def test_managed_process_identity_rejects_unrelated_process(monkeypatch):
    monkeypatch.setattr(server_boot.sys, 'platform', 'win32')
    monkeypatch.setattr(
        server_boot.subprocess,
        'run',
        lambda *args, **kwargs: type(
            'Result',
            (),
            {'returncode': 0, 'stdout': json.dumps({'Name': 'python.exe', 'CommandLine': 'python unrelated.py'})},
        )(),
    )

    assert server_boot.managed_process_identity(1234) is False


def test_managed_process_identity_accepts_llama_process(monkeypatch):
    monkeypatch.setattr(server_boot.sys, 'platform', 'win32')
    monkeypatch.setattr(
        server_boot.subprocess,
        'run',
        lambda *args, **kwargs: type(
            'Result',
            (),
            {'returncode': 0, 'stdout': json.dumps({
                'Name': 'llama-server.exe',
                'CommandLine': r'C:\dev\Dflash\llama-server.exe --port 8090',
            })},
        )(),
    )

    assert server_boot.managed_process_identity(1234) is True


def test_wait_for_port_closed_retries_until_listener_is_gone(monkeypatch):
    states = iter([True, True, False])
    monkeypatch.setattr(server_boot, '_tcp_port_open', lambda host, port: next(states))
    monkeypatch.setattr(server_boot.time, 'sleep', lambda seconds: None)

    assert server_boot.wait_for_port_closed('127.0.0.1', 8090, timeout=1) is True
