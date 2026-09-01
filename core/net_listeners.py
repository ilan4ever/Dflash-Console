"""Loopback TCP listener discovery without spawning netstat.exe.

Windows netstat can fail with 0xc0000142 (DLL init failure) and shows a modal
Application Error dialog that freezes the desktop. Prefer psutil or PowerShell
Get-NetTCPConnection instead.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

_LISTEN_PORTS_CACHE: tuple[float, dict[int, list[int]]] = (0.0, {})
_LISTEN_PORTS_TTL_SECONDS = 3.0
_LISTEN_PORTS_LOCK = threading.Lock()


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'startupinfo': startupinfo, 'creationflags': flags}
    return {}


def _loopback_hosts() -> set[str]:
    return {'127.0.0.1', '0.0.0.0', '::', '::1', '*'}


def _listening_ports_map_psutil() -> dict[int, list[int]] | None:
    try:
        import psutil
    except ImportError:
        return None
    mapping: dict[int, list[int]] = {}
    loopback = _loopback_hosts()
    try:
        connections = psutil.net_connections(kind='tcp')
    except Exception:
        return None
    for conn in connections:
        status = getattr(conn, 'status', None)
        listen_state = getattr(psutil, 'CONN_LISTEN', 'LISTEN')
        if status != listen_state and str(status).upper() not in {'LISTEN', 'LISTENING'}:
            continue
        laddr = getattr(conn, 'laddr', None)
        if not laddr:
            continue
        host = str(getattr(laddr, 'host', laddr[0] if isinstance(laddr, tuple) else '') or '')
        port = int(getattr(laddr, 'port', laddr[1] if isinstance(laddr, tuple) and len(laddr) > 1 else 0) or 0)
        if port <= 0:
            continue
        if host and host not in loopback:
            continue
        pid = int(conn.pid or 0)
        if pid <= 0:
            continue
        mapping.setdefault(pid, []).append(port)
    return {pid: sorted(set(ports)) for pid, ports in mapping.items()}


def _listening_ports_map_powershell() -> dict[int, list[int]]:
    script = (
        "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue "
        "| Where-Object { $_.LocalAddress -in @('127.0.0.1','0.0.0.0','::','::1') } "
        "| ForEach-Object { Write-Output (\"{0}:{1}\" -f $_.OwningProcess, $_.LocalPort) }"
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return {}
    if result.returncode != 0 and not result.stdout.strip():
        return {}
    mapping: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        token = line.strip()
        if ':' not in token:
            continue
        pid_text, port_text = token.rsplit(':', 1)
        try:
            pid = int(pid_text)
            port = int(port_text)
        except (TypeError, ValueError):
            continue
        if pid > 0 and port > 0:
            mapping.setdefault(pid, []).append(port)
    return {pid: sorted(set(ports)) for pid, ports in mapping.items()}


def _listening_ports_map_lsof() -> dict[int, list[int]]:
    try:
        result = subprocess.run(
            ['lsof', '-Pan', '-iTCP', '-sTCP:LISTEN'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return {}
    import re

    mapping: dict[int, list[int]] = {}
    for line in result.stdout.splitlines()[1:]:
        match = re.search(r'^(\S+)\s+(\d+)\s+.*:(\d+)\s+\(LISTEN\)', line)
        if not match:
            continue
        try:
            pid = int(match.group(2))
            port = int(match.group(3))
        except (TypeError, ValueError):
            continue
        mapping.setdefault(pid, []).append(port)
    return {pid: sorted(set(ports)) for pid, ports in mapping.items()}


def listening_ports_map(*, force: bool = False) -> dict[int, list[int]]:
    """Return pid -> listening loopback TCP ports (cached snapshot)."""
    global _LISTEN_PORTS_CACHE
    now = time.time()
    with _LISTEN_PORTS_LOCK:
        cached_at, cached = _LISTEN_PORTS_CACHE
        if not force and cached and (now - cached_at) < _LISTEN_PORTS_TTL_SECONDS:
            return {pid: list(ports) for pid, ports in cached.items()}

        mapping: dict[int, list[int]] = {}
        if sys.platform == 'win32':
            mapping = _listening_ports_map_psutil() or _listening_ports_map_powershell()
        else:
            mapping = _listening_ports_map_lsof()

        if not mapping and cached:
            return {pid: list(ports) for pid, ports in cached.items()}
        _LISTEN_PORTS_CACHE = (now, {pid: list(ports) for pid, ports in mapping.items()})
        return {pid: list(ports) for pid, ports in mapping.items()}


def loopback_listening_ports() -> set[int]:
    ports: set[int] = set()
    for port_list in listening_ports_map().values():
        ports.update(port_list)
    return ports


def pid_listening_on_port(port: int, host: str = '127.0.0.1') -> int | None:
    if int(port or 0) <= 0:
        return None
    host = str(host or '127.0.0.1')
    if host not in _loopback_hosts() and host not in {'localhost'}:
        return None
    for pid, ports in listening_ports_map().items():
        if int(port) in ports:
            return int(pid)
    return None
