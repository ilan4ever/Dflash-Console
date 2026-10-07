"""Tests for loopback listener discovery without a full connection-table scan."""

from __future__ import annotations

from core import net_listeners


def test_listening_ports_map_uses_cache(monkeypatch):
    calls = {'count': 0}

    def fake_netstat() -> dict[int, int]:
        calls['count'] += 1
        return {8911: 1234}

    monkeypatch.setattr(net_listeners.sys, 'platform', 'win32')
    monkeypatch.setattr(net_listeners, '_netstat_listen_pids', fake_netstat)
    net_listeners._LISTEN_PORTS_CACHE = (0.0, {})
    first = net_listeners.listening_ports_map(force=True)
    second = net_listeners.listening_ports_map()
    assert first == {1234: [8911]}
    assert second == {1234: [8911]}
    assert calls['count'] == 1


def test_pid_listening_on_port_reads_snapshot(monkeypatch):
    monkeypatch.setattr(
        net_listeners,
        '_pid_listening_on_port_windows_fast',
        lambda port: None,
    )
    monkeypatch.setattr(
        net_listeners,
        'listening_ports_map',
        lambda **kwargs: {999: [8911, 9000]},
    )
    assert net_listeners.pid_listening_on_port(8911) == 999
    assert net_listeners.pid_listening_on_port(8080) is None


def test_configured_listening_ports_probes_deduped_targets(monkeypatch):
    import socket as sockmod

    probed: list[tuple[str, int, float]] = []

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_connect(addr, timeout=0.25):
        host, port = addr[0], addr[1]
        probed.append((host, port, float(timeout)))
        if port == 9001:
            raise OSError("refused")
        return FakeSock()

    monkeypatch.setattr(sockmod, "create_connection", fake_connect)
    servers = [
        {"host": "127.0.0.1", "port": 9000},
        {"host": "127.0.0.1", "port": 9000},
        {"host": "127.0.0.1", "port": 9001},
        {"host": "127.0.0.1", "port": 0},
        {"port": -1},
    ]
    open_ports = net_listeners.configured_listening_ports(servers)
    assert open_ports == {9000}
    assert sorted(port for _, port, _ in probed) == [9000, 9001]
    assert all(timeout == 0.25 for _, _, timeout in probed)

def test_pid_listening_on_port_prefers_windows_fast_lookup(monkeypatch):
    monkeypatch.setattr(net_listeners.sys, 'platform', 'win32')
    monkeypatch.setattr(
        net_listeners,
        '_pid_listening_on_port_windows_fast',
        lambda port: 4242 if int(port) == 8095 else None,
    )
    calls = {'map': 0}

    def fake_map(**kwargs):
        calls['map'] += 1
        return {}

    monkeypatch.setattr(net_listeners, 'listening_ports_map', fake_map)
    assert net_listeners.pid_listening_on_port(8095) == 4242
    assert calls['map'] == 0

