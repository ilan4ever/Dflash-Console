"""Tests for loopback listener discovery without netstat."""

from __future__ import annotations

from core import net_listeners


def test_listening_ports_map_uses_cache(monkeypatch):
    calls = {'count': 0}

    def fake_powershell() -> dict[int, list[int]]:
        calls['count'] += 1
        return {1234: [8911]}

    monkeypatch.setattr(net_listeners, '_listening_ports_map_powershell', fake_powershell)
    monkeypatch.setattr(net_listeners, '_listening_ports_map_psutil', lambda: None)
    net_listeners._LISTEN_PORTS_CACHE = (0.0, {})
    first = net_listeners.listening_ports_map(force=True)
    second = net_listeners.listening_ports_map()
    assert first == {1234: [8911]}
    assert second == {1234: [8911]}
    assert calls['count'] == 1


def test_pid_listening_on_port_reads_snapshot(monkeypatch):
    monkeypatch.setattr(
        net_listeners,
        'listening_ports_map',
        lambda **kwargs: {999: [8911, 9000]},
    )
    assert net_listeners.pid_listening_on_port(8911) == 999
    assert net_listeners.pid_listening_on_port(8080) is None
