"""Stop other Console API listeners so `dflash serve` owns one port."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dflash_cli.http import ConsoleClient, ConsoleError


def console_health(port: int, host: str = '127.0.0.1') -> dict[str, Any] | None:
    try:
        payload = ConsoleClient(f'http://{host}:{int(port)}', timeout=2).get('/api/health')
    except ConsoleError:
        return None
    if not isinstance(payload, dict):
        return None
    if (
        payload.get('success') is True
        and str(payload.get('app') or '') == 'DFlash Console'
        and str(payload.get('boot_id') or '').strip()
    ):
        return payload
    return None


def console_roots_match(health: dict[str, Any], target_root: Path) -> bool:
    target = target_root.expanduser().resolve()
    for key in ('process_root', 'console_root'):
        raw = str(health.get(key) or '').strip()
        if not raw:
            continue
        try:
            if Path(raw).expanduser().resolve() == target:
                return True
        except OSError:
            continue
    return False


def console_versions_match(health: dict[str, Any], cli_version: str) -> bool:
    server_version = str(health.get('version') or health.get('shell_version') or '').strip()
    return bool(server_version) and server_version == str(cli_version).strip()


def tcp_port_open(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=0.4):
            return True
    except OSError:
        return False


def stop_console_on_port(port: int, host: str = '127.0.0.1', *, timeout: float = 20.0) -> bool:
    """Graceful /api/shutdown, then force-stop a Console uvicorn listener."""
    base = f'http://{host}:{int(port)}'
    if console_health(port, host):
        try:
            ConsoleClient(base, timeout=3).post('/api/shutdown')
        except ConsoleError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not console_health(port, host) and not tcp_port_open(host, port):
                return True
            time.sleep(0.3)
    if not tcp_port_open(host, port):
        return True
    return _force_stop_console_listener(port, host)


def _force_stop_console_listener(port: int, host: str = '127.0.0.1') -> bool:
    pid = _listener_pid(port, host)
    if pid is None:
        return not tcp_port_open(host, port)
    if not _is_console_listener(pid):
        return False
    try:
        if sys.platform == 'win32':
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            os.kill(pid, 9)
    except Exception:
        return False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not tcp_port_open(host, port):
            return True
        time.sleep(0.1)
    return not tcp_port_open(host, port)


def _listener_pid(port: int, host: str = '127.0.0.1') -> int | None:
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'tcp'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return None
        needle = f':{int(port)}'
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or 'LISTENING' not in parts:
                continue
            local_addr = parts[1]
            if not local_addr.endswith(needle):
                continue
            if local_addr.startswith('127.0.0.1') or local_addr.startswith('0.0.0.0') or local_addr.startswith('[::]'):
                try:
                    return int(parts[-1])
                except ValueError:
                    return None
        return None
    try:
        result = subprocess.run(
            ['lsof', '-ti', f'tcp:{int(port)}'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.stdout.strip():
            return int(result.stdout.strip().split('\n')[0])
    except Exception:
        return None
    return None


def _is_console_listener(pid: int) -> bool:
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\").CommandLine",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            command_line = result.stdout.strip().lower()
        except Exception:
            return False
        return 'uvicorn' in command_line and 'api.app:app' in command_line
    try:
        with open(f'/proc/{int(pid)}/cmdline', 'rb') as handle:
            command_line = handle.read().decode('utf-8', errors='ignore').lower()
        return 'uvicorn' in command_line and 'api.app:app' in command_line
    except Exception:
        return False
