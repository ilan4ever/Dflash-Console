from __future__ import annotations

import json
from unittest.mock import patch

from core.node_connect import (
    _parse_tailscale_status,
    build_connect_wizard,
    build_ssh_tunnel_commands,
    detect_tailscale,
    probe_console_url,
)


def test_parse_tailscale_status_extracts_self_and_peers():
    payload = {
        'Self': {
            'Online': True,
            'HostName': 'office-pc',
            'DNSName': 'office-pc.tailnet.ts.net',
            'TailscaleIPs': ['100.64.0.2', 'fd7a:115c:a1e0::2'],
        },
        'Peer': {
            'abc': {
                'Online': True,
                'HostName': 'lab-gpu',
                'DNSName': 'lab-gpu.tailnet.ts.net',
                'TailscaleIPs': ['100.64.0.5'],
            },
            'def': {
                'Online': False,
                'HostName': 'offline-pc',
                'TailscaleIPs': ['100.64.0.9'],
            },
        },
    }
    parsed = _parse_tailscale_status(payload)
    assert parsed['self_ip'] == '100.64.0.2'
    assert parsed['self_hostname'] == 'office-pc'
    assert len(parsed['peers']) == 2
    assert parsed['peers'][0]['ipv4'] == '100.64.0.5'
    assert parsed['peers'][0]['online'] is True


def test_build_ssh_tunnel_commands_reach_remote():
    row = build_ssh_tunnel_commands(
        scenario='reach_remote',
        ui_port=8900,
        ssh_user='alice',
        ssh_host='gpu.example.com',
        local_bind_port=8901,
        remote_console_port=8900,
    )
    assert row['scenario'] == 'reach_remote'
    assert '-L 8901:127.0.0.1:8900' in row['command']
    assert row['node_url_for_remote_operator'] == 'http://127.0.0.1:8901'


def test_build_ssh_tunnel_commands_share_local():
    row = build_ssh_tunnel_commands(
        scenario='share_local',
        ui_port=8900,
        ssh_user='bob',
        ssh_host='bastion.example.com',
        remote_console_port=8900,
    )
    assert row['scenario'] == 'share_local'
    assert '-R 8900:127.0.0.1:8900' in row['command']


def test_detect_tailscale_missing_binary():
    with patch('core.node_connect.shutil.which', return_value=None):
        row = detect_tailscale()
    assert row['installed'] is False
    assert 'not installed' in row['error'].lower()


def test_detect_tailscale_parses_json():
    payload = {
        'Self': {'Online': True, 'TailscaleIPs': ['100.64.0.3'], 'HostName': 'home'},
        'Peer': {},
    }

    class Proc:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ''

    with patch('core.node_connect.shutil.which', return_value='tailscale'), patch(
        'core.node_connect.subprocess.run',
        return_value=Proc(),
    ):
        row = detect_tailscale()
    assert row['installed'] is True
    assert row['self_ip'] == '100.64.0.3'


def test_build_connect_wizard_includes_methods():
    with patch('core.node_connect.detect_tailscale', return_value={'installed': False, 'running': False, 'self_ip': '', 'peers': [], 'error': ''}):
        row = build_connect_wizard(cfg={'ui_port': 8900})
    assert row['ui_port'] == 8900
    assert row['ssh']['reach_remote']['command']
    assert row['ssh']['share_local']['command']


def test_probe_console_url_delegates_to_health_check():
    with patch('core.node_connect.check_remote_node_health', return_value={'online': True, 'status': 'online', 'remote_version': '1.2.3'}):
        row = probe_console_url('http://127.0.0.1:8900')
    assert row['success'] is True
    assert row['remote_version'] == '1.2.3'
