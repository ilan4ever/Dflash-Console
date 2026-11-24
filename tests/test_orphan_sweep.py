"""The startup orphan sweep stops only Console engines: never other apps or another Console's API."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

import core.runtime as runtime_module
from core import server_boot

PROCESSES = {
    101: ('llama-server', '/home/u/dev/llama.cpp/build/bin/llama-server --host 127.0.0.1 --port 8091'),
    102: ('python3', 'python3 -m http.server 8977 --bind 127.0.0.1'),
    103: ('python3', '/home/u/dev/Dflash-Console/.venv/bin/python3 -m uvicorn api.app:app --host 127.0.0.1 --port 8900'),
    104: ('ollama', '/usr/local/bin/ollama serve'),
    105: ('lm-studio', '/opt/LM Studio/lm-studio --server'),
}


@pytest.fixture
def linux(monkeypatch):
    """Act as Linux for server_boot only, with a fixed process table."""
    monkeypatch.setattr(server_boot, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(server_boot, '_posix_process_details', lambda pid: PROCESSES.get(int(pid)))


def test_linux_candidate_needs_an_engine_marker(linux):
    assert server_boot.orphan_sweep_candidate(101)
    assert not server_boot.orphan_sweep_candidate(102)  # another app
    assert not server_boot.orphan_sweep_candidate(103)  # another Console's API
    assert not server_boot.orphan_sweep_candidate(104)  # the user's own Ollama
    assert not server_boot.orphan_sweep_candidate(105)  # LM Studio
    assert not server_boot.orphan_sweep_candidate(999)  # process already gone


def test_sweep_stops_only_engines_on_unconfigured_ports(linux, monkeypatch):
    listeners = {101: [8091], 102: [8977], 103: [8900, 8001], 104: [11434], os.getpid(): [8962, 8963]}
    monkeypatch.setattr('core.net_listeners.listening_ports_map', lambda force=False: listeners)
    stopped: list[int] = []

    def fake_stop(*, port, host='127.0.0.1', **_kw):
        stopped.append(int(port))
        return {'success': True}

    monkeypatch.setattr(runtime_module, 'stop_server', fake_stop)
    cfg = {'ui_port': 8962, 'gateway_port': 8963, 'servers': [], 'runtimes': []}
    results = server_boot.sweep_orphan_managed_listeners(cfg=cfg)
    assert stopped == [8091]
    assert [row['port'] for row in results] == [8091]


def test_configured_engine_port_is_kept(linux, monkeypatch):
    monkeypatch.setattr('core.net_listeners.listening_ports_map', lambda force=False: {101: [8091]})
    stopped: list[int] = []
    monkeypatch.setattr(runtime_module, 'stop_server', lambda *, port, **_kw: stopped.append(port) or {'success': True})
    cfg = {'ui_port': 8962, 'gateway_port': 8963, 'servers': [{'id': 'srv', 'port': 8091}], 'runtimes': []}
    server_boot.sweep_orphan_managed_listeners(cfg=cfg)
    assert stopped == []


def test_windows_keeps_the_managed_identity_check(monkeypatch):
    monkeypatch.setattr(server_boot, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(server_boot, 'managed_process_identity', lambda pid: pid == 7)
    assert server_boot.orphan_sweep_candidate(7)
    assert not server_boot.orphan_sweep_candidate(8)


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='reads /proc')
def test_reads_a_real_process_on_linux():
    name, command = server_boot._posix_process_details(os.getpid())
    assert name
    assert 'pytest' in command or 'python' in command
    # The test runner itself carries no engine marker.
    assert not server_boot.orphan_sweep_candidate(os.getpid())
