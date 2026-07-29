"""Port probes, model discovery, and server stop helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from core.gpu_devices import format_gpu_assignment, query_gpu_devices, resolve_role_gpu_launch_params
from core.load_progress import is_active_boot, parse_load_progress, read_log_tail
from core.model_presets import gpu_layers_max_for
from core.model_stack import resolve_model_stack
from core.server_boot import clear_server_tracking, get_started_launch, adopt_running_engine


def _kill_listener_on_port(port: int, host: str = '127.0.0.1') -> bool:
    import socket as _socket

    try:
        with _socket.create_connection((host, int(port)), timeout=0.4):
            pass
    except OSError:
        return False

    pid = None
    try:
        if sys.platform == 'win32':
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'tcp'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            needle = f':{int(port)}'
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) < 5 or 'LISTENING' not in parts[3]:
                    continue
                local_addr = parts[1]
                if not local_addr.endswith(needle):
                    continue
                if local_addr.startswith('127.0.0.1') or local_addr.startswith('0.0.0.0') or local_addr.startswith('[::]'):
                    try:
                        pid = int(parts[4])
                    except (ValueError, IndexError):
                        pid = None
                    break
        else:
            result = subprocess.run(
                ['lsof', '-ti', f'tcp:{int(port)}'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.stdout.strip():
                pid = int(result.stdout.strip().split('\n')[0])
    except Exception:
        pid = None

    if pid is None:
        return False

    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True, timeout=5, check=False)
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False


def tcp_port_open(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def _fetch_models_payload(api_url: str) -> list[dict[str, Any]]:
    url = f"{str(api_url or '').strip().rstrip('/')}/models"
    try:
        with urllib.request.urlopen(url, timeout=2.5) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError):
        return []
    models = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    return [entry for entry in models if isinstance(entry, dict)]


def _model_state(entry: dict[str, Any]) -> str:
    status = entry.get('status')
    if isinstance(status, dict):
        return str(status.get('value') or '').lower()
    return ''


def router_unload_available(api_url: str) -> bool:
    url = f'{api_base_url(api_url)}/models/unload'
    request = urllib.request.Request(
        url,
        data=b'{}',
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.5):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code != 404
    except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError):
        return False


def probe_models(api_url: str) -> list[str]:
    entries = _fetch_models_payload(api_url)
    router = router_unload_available(api_url)
    ids: list[str] = []
    for entry in entries:
        model_id = str(entry.get('id') or entry.get('model') or '').strip()
        if not model_id or model_id == 'default':
            continue
        if router:
            state = _model_state(entry)
            if state:
                if state not in ('loaded', 'running'):
                    continue
            elif not (isinstance(entry.get('meta'), dict) and entry['meta'].get('n_ctx')):
                continue
        ids.append(model_id)
    return ids


def probe_loading_models(api_url: str) -> list[str]:
    ids: list[str] = []
    for entry in _fetch_models_payload(api_url):
        if _model_state(entry) != 'loading':
            continue
        model_id = str(entry.get('id') or entry.get('model') or '').strip()
        if model_id and model_id != 'default':
            ids.append(model_id)
    return ids


def api_base_url(api_url: str) -> str:
    parsed = urlparse(str(api_url or '').strip().rstrip('/'))
    path = parsed.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    base = f'{parsed.scheme}://{parsed.netloc}{path}'.rstrip('/')
    return base or f'{parsed.scheme}://{parsed.netloc}'


def unload_model(*, api_url: str, model_id: str) -> dict[str, Any]:
    model = str(model_id or '').strip()
    if not model:
        return {'success': False, 'error': 'model_id required'}
    url = f'{api_base_url(api_url)}/models/unload'
    body = json.dumps({'model': model}).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        return {'success': False, 'error': detail or str(exc), 'http_status': exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError) as exc:
        return {'success': False, 'error': str(exc)}
    return {'success': True, 'unloaded': True, 'model': model, 'response': payload}


def load_model(*, api_url: str, model_id: str) -> dict[str, Any]:
    model = str(model_id or '').strip()
    if not model:
        return {'success': False, 'error': 'model_id required'}
    url = f'{api_base_url(api_url)}/models/load'
    body = json.dumps({'model': model}).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace') or '{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        return {'success': False, 'error': detail or str(exc), 'http_status': exc.code}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, ConnectionResetError, OSError) as exc:
        return {'success': False, 'error': str(exc)}
    return {'success': True, 'loaded': True, 'model': model, 'response': payload}


def stop_server(*, port: int | None = None, api_url: str | None = None, host: str = '127.0.0.1') -> dict[str, Any]:
    resolved_port = int(port or 0)
    if not resolved_port and api_url:
        parsed = urlparse(str(api_url))
        resolved_port = int(parsed.port or 0)
        host = str(parsed.hostname or host)
    if resolved_port <= 0:
        return {'success': False, 'error': 'port or api_url required'}

    killed = _kill_listener_on_port(resolved_port, host)
    clear_server_tracking(resolved_port)
    return {
        'success': True,
        'port': resolved_port,
        'host': host,
        'stopped': killed or not tcp_port_open(host, resolved_port),
    }


def _annotate_model_stack(
    stack: list[dict[str, Any]],
    *,
    booting: bool,
    loaded_models: list[str],
    progress: float | None,
) -> list[dict[str, Any]]:
    alias_ready = bool(loaded_models)
    result: list[dict[str, Any]] = []
    for entry in stack:
        row = dict(entry)
        role = str(entry.get('role') or '')
        if alias_ready:
            row['card_state'] = 'ready'
            row['progress'] = None
            row['ejectable'] = role == 'alias'
        elif booting:
            row['card_state'] = 'loading'
            row['progress'] = progress if role in ('alias', 'target') else None
            row['ejectable'] = role == 'alias'
        else:
            row['card_state'] = 'idle'
            row['progress'] = None
            row['ejectable'] = False
        result.append(row)
    return result


def _stack_detail(entry: dict[str, Any]) -> dict[str, Any]:
    path = str(entry.get('path') or '')
    filename = path.replace('\\', '/').split('/')[-1] if path else str(entry.get('id') or entry.get('label') or '')
    return {
        'role': entry.get('role'),
        'label': entry.get('label') or entry.get('role'),
        'name': filename or str(entry.get('id') or ''),
        'size_gb': entry.get('size_gb'),
        'source': entry.get('source'),
    }


def _build_visible_cards(
    model_stack: list[dict[str, Any]],
    *,
    server_label: str,
    booting: bool,
    loaded_models: list[str],
    progress: float | None,
) -> list[dict[str, Any]]:
    alias_rows = [row for row in model_stack if row.get('role') == 'alias']
    alias = alias_rows[0] if alias_rows else None
    alias_ready = bool(loaded_models)

    if alias_ready and alias:
        parts = [row for row in model_stack if row.get('role') != 'alias']
        composite = {
            **alias,
            'title': server_label or str(alias.get('id') or 'Loaded model'),
            'subtitle': f"API: {alias.get('id')}",
            'stack_details': [_stack_detail(row) for row in parts],
            'card_state': 'ready',
            'progress': None,
            'ejectable': True,
        }
        return [composite]

    if booting and alias:
        parts = [row for row in model_stack if row.get('role') != 'alias']
        composite = {
            **alias,
            'title': server_label or str(alias.get('id') or 'Loading model'),
            'subtitle': 'Loading…',
            'stack_details': [_stack_detail(row) for row in parts],
            'card_state': 'loading',
            'progress': progress,
            'ejectable': True,
        }
        return [composite]

    return [row for row in model_stack if row.get('card_state') in ('ready', 'loading')]


def build_server_status(server: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    host = str(server.get('host') or '127.0.0.1')
    port = int(server.get('port') or 0)
    api_url = str(server.get('api_url') or '')
    server_id = str(server.get('id') or '')
    gpus = query_gpu_devices()
    launch = resolve_role_gpu_launch_params(
        server.get('gpu_device'),
        model_id=server.get('model_id'),
        gpus=gpus,
        hardware=(cfg or {}).get('hardware_settings'),
    )
    gpu_display = format_gpu_assignment(str(server.get('gpu_device') or 'auto'), launch, gpus)
    running = tcp_port_open(host, port) if port > 0 else False
    if running and not get_started_launch(port):
        adopt_running_engine(server, cfg=cfg)
    loaded_models = probe_models(api_url) if running else []
    loading_models = probe_loading_models(api_url) if running else []
    router_ready = running and router_unload_available(api_url)
    log_lines = read_log_tail(server_id) if server_id else []
    active_boot = is_active_boot(log_lines)
    load_progress = parse_load_progress(log_lines) if active_boot else None
    alias_ready = bool(loaded_models)
    booting = running and not alias_ready and (
        bool(loading_models) or (active_boot and not router_ready)
    )
    started = get_started_launch(port)
    status = 'stopped'
    if running and loaded_models:
        status = 'loaded'
    elif booting:
        status = 'booting'
    elif running:
        status = 'running'

    stack = resolve_model_stack(server, cfg=cfg)
    model_stack = _annotate_model_stack(
        stack,
        booting=booting,
        loaded_models=loaded_models,
        progress=load_progress,
    )
    visible_cards = _build_visible_cards(
        model_stack,
        server_label=str(server.get('label') or ''),
        booting=booting,
        loaded_models=loaded_models,
        progress=load_progress,
    )

    from core.inference_stats import fetch_inference_stats

    inference_stats = fetch_inference_stats(api_url, server_id=str(server.get('id') or '')) if running and loaded_models else {}

    return {
        **server,
        'running': running,
        'status': status,
        'booting': booting,
        'load_progress': load_progress,
        'loaded_models': loaded_models,
        'model_stack': model_stack,
        'visible_cards': visible_cards,
        'gpu_display': gpu_display,
        'launch': started or {
            'context': server.get('context_size'),
            'main_gpu': launch.get('main_gpu'),
            'split_mode': launch.get('split_mode'),
            'tensor_split': launch.get('tensor_split'),
            'idle_unload_seconds': int(server.get('idle_unload_minutes') or 0) * 60,
        },
        'active_gpu_index': started.get('main_gpu') if started else launch.get('main_gpu'),
        'reachable_url': f'http://{host}:{port}' if port > 0 else '',
        'gpu_layers_max': gpu_layers_max_for(server, cfg=cfg),
        'inference_stats': inference_stats,
    }


def get_status_payload(servers: list[dict[str, Any]], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = [s for s in servers if s.get('enabled', True)]
    primary_id = enabled[0]['id'] if enabled else (servers[0]['id'] if servers else '')
    return {
        'success': True,
        'servers': [build_server_status(server, cfg=cfg) for server in servers],
        'primary_server_id': primary_id,
        'updated_at': __import__('time').time(),
    }
