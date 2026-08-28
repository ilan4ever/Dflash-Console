"""Remote DFlash Console node registry, health checks, and chat proxy helpers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from core.config import load_config, normalize_remote_node, normalize_remote_nodes, save_config


def _node_headers(node: dict[str, Any]) -> dict[str, str]:
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'DFlash-Console/remote-node',
    }
    token = str(node.get('api_token') or '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def _public_node(node: dict[str, Any], *, health: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        'id': node.get('id'),
        'label': node.get('label'),
        'base_url': node.get('base_url'),
        'enabled': node.get('enabled') is not False,
        'has_token': bool(str(node.get('api_token') or '').strip()),
    }
    if health:
        row.update(health)
    return row


def list_remote_nodes(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = cfg or load_config()
    return list(normalize_remote_nodes(config.get('remote_nodes')))


def get_remote_node(node_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    key = str(node_id or '').strip()
    if not key:
        return None
    for row in list_remote_nodes(cfg):
        if row.get('id') == key:
            return row
    return None


def save_remote_nodes(nodes: list[dict[str, Any]], *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = dict(cfg or load_config())
    normalized = normalize_remote_nodes(nodes)
    config['remote_nodes'] = normalized
    save_config(config)
    return normalized


def add_remote_node(payload: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    config = cfg or load_config()
    existing = list_remote_nodes(config)
    taken = {str(row.get('id') or '') for row in existing}
    node = normalize_remote_node(payload, existing_ids=taken)
    existing.append(node)
    save_remote_nodes(existing, cfg=config)
    return node


def update_remote_node(node_id: str, payload: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    config = cfg or load_config()
    existing = list_remote_nodes(config)
    key = str(node_id or '').strip()
    updated: dict[str, Any] | None = None
    next_rows: list[dict[str, Any]] = []
    taken = {str(row.get('id') or '') for row in existing if str(row.get('id') or '') != key}
    for row in existing:
        if str(row.get('id') or '') != key:
            next_rows.append(row)
            continue
        merged = {
            **row,
            **{k: v for k, v in (payload or {}).items() if v is not None},
            'id': key,
        }
        if payload.get('api_token') == '':
            merged['api_token'] = ''
        updated = normalize_remote_node(merged, existing_ids=taken)
        next_rows.append(updated)
    if not updated:
        return None
    save_remote_nodes(next_rows, cfg=config)
    return updated


def remove_remote_node(node_id: str, *, cfg: dict[str, Any] | None = None) -> bool:
    config = cfg or load_config()
    key = str(node_id or '').strip()
    existing = list_remote_nodes(config)
    next_rows = [row for row in existing if str(row.get('id') or '') != key]
    if len(next_rows) == len(existing):
        return False
    save_remote_nodes(next_rows, cfg=config)
    return True


def node_chat_url(node: dict[str, Any]) -> str:
    base = str(node.get('base_url') or '').strip().rstrip('/')
    return f'{base}/v1/chat/completions'


def node_health_url(node: dict[str, Any]) -> str:
    base = str(node.get('base_url') or '').strip().rstrip('/')
    return f'{base}/api/health'


def check_remote_node_health(node: dict[str, Any], *, timeout: float = 8.0) -> dict[str, Any]:
    if node.get('enabled') is False:
        return {
            'status': 'disabled',
            'online': False,
            'checked_at': time.time(),
            'error': 'Node is disabled',
        }
    url = node_health_url(node)
    started = time.time()
    try:
        req = urllib.request.Request(url, headers=_node_headers(node), method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        payload = json.loads(raw or '{}')
        if not isinstance(payload, dict) or payload.get('success') is False:
            return {
                'status': 'error',
                'online': False,
                'checked_at': time.time(),
                'latency_ms': int((time.time() - started) * 1000),
                'error': str(payload.get('error') or 'Unexpected health response'),
            }
        return {
            'status': 'online',
            'online': True,
            'checked_at': time.time(),
            'latency_ms': int((time.time() - started) * 1000),
            'remote_version': str(payload.get('version') or ''),
            'remote_app': str(payload.get('app') or ''),
            'remote_boot_id': str(payload.get('boot_id') or ''),
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        return {
            'status': 'error',
            'online': False,
            'checked_at': time.time(),
            'latency_ms': int((time.time() - started) * 1000),
            'error': detail or f'HTTP {exc.code}',
        }
    except Exception as exc:
        return {
            'status': 'offline',
            'online': False,
            'checked_at': time.time(),
            'latency_ms': int((time.time() - started) * 1000),
            'error': str(exc),
        }


def list_nodes_with_health(*, fresh: bool = False, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in list_remote_nodes(cfg):
        if fresh:
            health = check_remote_node_health(node)
        elif node.get('enabled') is False:
            health = {
                'status': 'disabled',
                'online': False,
            }
        else:
            health = {
                'status': 'unknown',
                'online': False,
            }
        rows.append(_public_node(node, health=health))
    return rows
