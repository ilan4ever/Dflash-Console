"""Secure remote node connection helpers — Tailscale detection and SSH tunnel recipes."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from typing import Any

from core.config import load_config
from core.remote_nodes import check_remote_node_health

_TAILSCALE_IPV4 = re.compile(r'^100\.\d{1,3}\.\d{1,3}\.\d{1,3}$')


def _pick_tailscale_ipv4(values: Any) -> str:
    if not isinstance(values, list):
        return ''
    for item in values:
        text = str(item or '').strip()
        if _TAILSCALE_IPV4.match(text):
            return text
    return ''


def _parse_tailscale_status(payload: dict[str, Any]) -> dict[str, Any]:
    self_row = payload.get('Self') if isinstance(payload.get('Self'), dict) else {}
    peers_raw = payload.get('Peer') if isinstance(payload.get('Peer'), dict) else {}
    peers: list[dict[str, Any]] = []
    for _peer_id, row in peers_raw.items():
        if not isinstance(row, dict):
            continue
        ipv4 = _pick_tailscale_ipv4(row.get('TailscaleIPs'))
        if not ipv4:
            continue
        peers.append({
            'hostname': str(row.get('HostName') or row.get('DNSName') or ipv4).strip(),
            'dns_name': str(row.get('DNSName') or '').strip(),
            'ipv4': ipv4,
            'online': row.get('Online') is not False,
        })
    peers.sort(key=lambda row: (not row.get('online'), str(row.get('hostname') or '').lower()))
    self_ip = _pick_tailscale_ipv4(self_row.get('TailscaleIPs'))
    return {
        'installed': True,
        'running': self_row.get('Online') is not False,
        'self_ip': self_ip,
        'self_dns': str(self_row.get('DNSName') or '').strip(),
        'self_hostname': str(self_row.get('HostName') or '').strip(),
        'peers': peers,
        'error': '',
    }


def _run_command(args: list[str], *, timeout: float = 4.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, '', str(exc)
    return int(proc.returncode), str(proc.stdout or ''), str(proc.stderr or '')


def detect_tailscale() -> dict[str, Any]:
    binary = shutil.which('tailscale')
    if not binary:
        return {
            'installed': False,
            'running': False,
            'self_ip': '',
            'self_dns': '',
            'self_hostname': '',
            'peers': [],
            'error': 'Tailscale is not installed on this PC.',
        }
    code, stdout, stderr = _run_command([binary, 'status', '--json'], timeout=5.0)
    if code != 0:
        detail = (stderr or stdout or 'Tailscale is installed but not running.').strip()
        return {
            'installed': True,
            'running': False,
            'self_ip': '',
            'self_dns': '',
            'self_hostname': '',
            'peers': [],
            'error': detail,
        }
    try:
        payload = json.loads(stdout or '{}')
    except json.JSONDecodeError:
        return {
            'installed': True,
            'running': False,
            'self_ip': '',
            'self_dns': '',
            'self_hostname': '',
            'peers': [],
            'error': 'Could not read Tailscale status.',
        }
    if not isinstance(payload, dict):
        return {
            'installed': True,
            'running': False,
            'self_ip': '',
            'self_dns': '',
            'self_hostname': '',
            'peers': [],
            'error': 'Unexpected Tailscale status response.',
        }
    parsed = _parse_tailscale_status(payload)
    parsed['installed'] = True
    if not parsed.get('self_ip') and not parsed.get('peers'):
        parsed['running'] = False
        parsed['error'] = parsed.get('error') or 'Tailscale is installed but this device has no tailnet IP yet.'
    return parsed


def build_ssh_tunnel_commands(
    *,
    scenario: str,
    ui_port: int,
    ssh_user: str,
    ssh_host: str,
    local_bind_port: int | None = None,
    remote_console_port: int | None = None,
) -> dict[str, Any]:
    user = str(ssh_user or '').strip() or 'user'
    host = str(ssh_host or '').strip() or 'example.com'
    console_port = int(remote_console_port or ui_port)
    bind_port = int(local_bind_port or (ui_port + 1))
    target = f'{user}@{host}'
    key = str(scenario or '').strip().lower()
    if key == 'share_local':
        command = (
            f'ssh -N -R {console_port}:127.0.0.1:{int(ui_port)} {target}'
        )
        return {
            'scenario': 'share_local',
            'command': command,
            'summary': 'Run on the PC that runs DFlash Console. A trusted SSH server receives the tunnel.',
            'node_url_for_remote_operator': f'http://127.0.0.1:{console_port}',
            'notes': [
                'The other Console must reach the SSH server and use the node URL shown above on that server.',
                'Keep this terminal open while the tunnel is active.',
                'Use SSH keys instead of passwords when possible.',
            ],
        }
    command = (
        f'ssh -N -L {bind_port}:127.0.0.1:{console_port} {target}'
    )
    return {
        'scenario': 'reach_remote',
        'command': command,
        'summary': 'Run on this PC. It opens a local port that forwards to the remote Console.',
        'node_url_for_remote_operator': f'http://127.0.0.1:{bind_port}',
        'notes': [
            'Replace user and host with the remote PC or jump box that can reach the Console.',
            'Keep this terminal open while you use the node.',
            'Traffic is encrypted inside the SSH tunnel.',
        ],
    }


def build_connect_wizard(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    ui_port = int(config.get('ui_port') or 8900)
    tailscale = detect_tailscale()
    local_bind_port = ui_port + 1
    self_url = ''
    if tailscale.get('self_ip'):
        self_url = f'http://{tailscale["self_ip"]}:{ui_port}'
    return {
        'success': True,
        'ui_port': ui_port,
        'platform': platform.system(),
        'local_console_url': f'http://127.0.0.1:{ui_port}',
        'tailscale': {
            **tailscale,
            'suggested_share_url': self_url,
            'install_url': 'https://tailscale.com/download',
            'docs_url': 'https://tailscale.com/kb/1017/install/',
        },
        'ssh': {
            'default_local_bind_port': local_bind_port,
            'reach_remote': build_ssh_tunnel_commands(
                scenario='reach_remote',
                ui_port=ui_port,
                ssh_user='user',
                ssh_host='remote.example.com',
                local_bind_port=local_bind_port,
                remote_console_port=ui_port,
            ),
            'share_local': build_ssh_tunnel_commands(
                scenario='share_local',
                ui_port=ui_port,
                ssh_user='user',
                ssh_host='bastion.example.com',
                remote_console_port=ui_port,
            ),
        },
    }


def probe_console_url(base_url: str, *, api_token: str = '', timeout: float = 8.0) -> dict[str, Any]:
    url = str(base_url or '').strip().rstrip('/')
    if not url:
        return {'success': False, 'online': False, 'status': 'error', 'error': 'Console URL is required'}
    node = {'base_url': url, 'enabled': True, 'api_token': str(api_token or '').strip()}
    health = check_remote_node_health(node, timeout=timeout)
    return {'success': health.get('online') is True, **health}
