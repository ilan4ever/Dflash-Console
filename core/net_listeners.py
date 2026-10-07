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
        cached_copy = {pid: list(ports) for pid, ports in cached.items()} if cached else {}

    if sys.platform == 'win32':
        # psutil and Get-NetTCPConnection walk every connection and can
        # stall for minutes right after boot. One netstat snapshot is enough.
        mapping: dict[int, list[int]] = {}
        for listen_port, pid in _netstat_listen_pids().items():
            mapping.setdefault(int(pid), []).append(int(listen_port))
        mapping = {pid: sorted(set(ports)) for pid, ports in mapping.items()}
    else:
        mapping = _listening_ports_map_lsof()

    if not mapping and cached_copy:
        return cached_copy
    with _LISTEN_PORTS_LOCK:
        _LISTEN_PORTS_CACHE = (time.time(), {pid: list(ports) for pid, ports in mapping.items()})
        return {pid: list(ports) for pid, ports in mapping.items()}



def configured_listening_ports(servers: list[dict]) -> set[int]:
    """Return ports that accept TCP connect among configured engine profiles.

    Dedupes (host, port) pairs with port > 0, then probes them in parallel
    (0.25s timeout, up to 16 workers). Much faster than a full Windows
    listener snapshot via psutil.net_connections / Get-NetTCPConnection.
    """
    import socket
    from concurrent.futures import ThreadPoolExecutor, as_completed

    targets: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for server in servers or []:
        if not isinstance(server, dict):
            continue
        host = str(server.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        try:
            port = int(server.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if port <= 0:
            continue
        key = (host, port)
        if key in seen:
            continue
        seen.add(key)
        targets.append(key)

    open_ports: set[int] = set()
    if not targets:
        return open_ports

    def _probe(host: str, port: int) -> int | None:
        try:
            with socket.create_connection((host, int(port)), timeout=0.25):
                return int(port)
        except OSError:
            return None

    workers = min(16, len(targets))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_probe, host, port) for host, port in targets]
        for fut in as_completed(futures):
            try:
                found = fut.result()
            except Exception:
                continue
            if found:
                open_ports.add(found)
    return open_ports


def loopback_listening_ports() -> set[int]:
    ports: set[int] = set()
    for port_list in listening_ports_map().values():
        ports.update(port_list)
    return ports



_NETSTAT_CACHE: tuple[float, dict[int, int]] = (0.0, {})
_NETSTAT_TTL_SECONDS = 2.0


def _netstat_listen_pids() -> dict[int, int]:
    """Port to process id, from one short netstat snapshot.

    Get-NetTCPConnection walks the whole connection table and can freeze the
    Console for minutes, which makes other apps think it is not running.
    """
    global _NETSTAT_CACHE
    now = time.time()
    with _LISTEN_PORTS_LOCK:
        cached_at, cached = _NETSTAT_CACHE
        if cached and (now - cached_at) < _NETSTAT_TTL_SECONDS:
            return dict(cached)
    try:
        from core.bounded_proc import run_bounded

        code, text = run_bounded(['netstat', '-ano', '-p', 'tcp'], timeout=2)
    except (OSError, subprocess.SubprocessError):
        return dict(cached)
    if code is None:
        return dict(cached)
    found: dict[int, int] = {}
    for line in text.splitlines():
        if 'LISTENING' not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 4 or ':' not in parts[1]:
            continue
        try:
            listen_port = int(parts[1].rsplit(':', 1)[-1])
            pid = int(parts[-1])
        except (TypeError, ValueError):
            continue
        if listen_port > 0 and pid > 0:
            found[listen_port] = pid
    with _LISTEN_PORTS_LOCK:
        _NETSTAT_CACHE = (now, found)
    return found


def _pid_listening_on_port_windows_fast(port: int) -> int | None:
    """Fast single-port owner lookup from a cached netstat snapshot."""
    port = int(port or 0)
    if port <= 0:
        return None
    pid = _netstat_listen_pids().get(port)
    return pid if pid else None


def pid_listening_on_port(port: int, host: str = '127.0.0.1') -> int | None:
    if int(port or 0) <= 0:
        return None
    host = str(host or '127.0.0.1')
    if host not in _loopback_hosts() and host not in {'localhost'}:
        return None
    if sys.platform == 'win32':
        fast = _pid_listening_on_port_windows_fast(int(port))
        if fast:
            return int(fast)
    for pid, ports in listening_ports_map().items():
        if int(port) in ports:
            return int(pid)
    return None
