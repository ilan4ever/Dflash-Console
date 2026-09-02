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


def test_ensure_managed_listen_port_rebinds_foreign_occupant(monkeypatch):
    server = {
        'id': 'qwen3-8-27b-q6-k-l',
        'host': '127.0.0.1',
        'port': 8090,
        'api_url': 'http://127.0.0.1:8090/v1',
    }
    saved: list[dict] = []

    monkeypatch.setattr(server_boot, '_tcp_port_open', lambda host, port: port == 8090)
    monkeypatch.setattr(server_boot, 'listener_is_managed_engine', lambda host, port: False)
    monkeypatch.setattr(
        'core.config.suggest_server_port',
        lambda cfg=None: 8097,
    )
    monkeypatch.setattr(
        'core.config.apply_server_listen_port',
        lambda server_id, port, cfg=None, persist=True: saved.append({'id': server_id, 'port': port, 'persist': persist}) or {
            'success': True,
            'port': port,
            'api_url': f'http://127.0.0.1:{port}/v1',
        },
    )

    result = server_boot.ensure_managed_listen_port(server, cfg={'servers': [dict(server)]})
    assert result['success'] is True
    assert result['reason'] == 'rebound'
    assert result['previous_port'] == 8090
    assert result['port'] == 8097
    assert server['port'] == 8097
    assert server['api_url'] == 'http://127.0.0.1:8097/v1'
    assert saved == [{'id': 'qwen3-8-27b-q6-k-l', 'port': 8097, 'persist': True}]
