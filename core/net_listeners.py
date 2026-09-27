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



def _pid_listening_on_port_windows_fast(port: int) -> int | None:
    """Fast single-port owner lookup (avoids full net_connections / listen map).

    venv often lacks psutil; the full Get-NetTCPConnection map is slow and can
    flake. Prefer a LocalPort-filtered query, then netstat for that port only.
    """
    port = int(port or 0)
    if port <= 0:
        return None
    script = (
        f"$c = @(Get-NetTCPConnection -State Listen -LocalPort {port} "
        "-ErrorAction SilentlyContinue | Select-Object -First 1); "
        "if ($c -and $c[0].OwningProcess) { Write-Output ([int]$c[0].OwningProcess) }"
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        result = None
    if result is not None:
        for line in result.stdout.splitlines():
            token = line.strip()
            if not token:
                continue
            try:
                pid = int(token)
            except (TypeError, ValueError):
                continue
            if pid > 0:
                return pid

    # netstat fallback scoped to the port (no modal dialog when CREATE_NO_WINDOW).
    try:
        result = subprocess.run(
            ['netstat', '-ano', '-p', 'tcp'],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            **_subprocess_no_window_kwargs(),
        )
    except Exception:
        return None
    needle = f':{port} '
    for line in result.stdout.splitlines():
        upper = line.upper()
        if 'LISTENING' not in upper and 'LISTEN' not in upper:
            continue
        if needle not in line and not line.rstrip().endswith(f':{port}'):
            # Match ":8095 " in local address column.
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[1] if len(parts) > 1 else ''
            if not local.endswith(f':{port}'):
                continue
        parts = line.split()
        try:
            pid = int(parts[-1])
        except (TypeError, ValueError):
            continue
        if pid > 0:
            return pid
    return None


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
