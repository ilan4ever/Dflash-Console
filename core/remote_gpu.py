"""Share a remote DFlash Console GPU over an SSH tunnel.

The model runs on the machine that owns the GPU. This PC only opens the
tunnel, asks that Console whether the file is already on disk, and loads it.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from core.remote_nodes import _node_headers, get_remote_node, list_remote_nodes

_REMOTE_GPU_ID = re.compile(r'^remote:([a-z0-9-]+):(\d+)$')
_TUNNEL_LOCK = threading.Lock()
_GPU_CACHE: dict[str, Any] = {'at': 0.0, 'gpus': []}
_GPU_CACHE_SECONDS = 20.0


def shared_gpu_label(name: str, tag: str) -> str:
    """GPU name with the machine in brackets, for example ``TITAN (production)``."""
    clean = str(name or '').strip() or 'GPU'
    mark = str(tag or 'remote').strip().lower() or 'remote'
    suffix = f'({mark})'
    if clean.lower().endswith(suffix):
        return clean
    return f'{clean} {suffix}'


def with_shared_gpus(local: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Local GPUs plus any production GPUs this Console is sharing."""
    rows = [dict(row) for row in (local or []) if isinstance(row, dict)]
    seen = {str(row.get('index')) for row in rows}
    for extra in remote_gpu_devices():
        key = str(extra.get('index'))
        if key in seen:
            continue
        seen.add(key)
        rows.append(dict(extra))
    return rows


def parse_remote_gpu_id(value: str) -> tuple[str, int] | None:
    match = _REMOTE_GPU_ID.match(str(value or '').strip().lower())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _basename(value: str) -> str:
    text = str(value or '').replace('\\', '/').strip()
    if not text:
        return ''
    return text.split('/')[-1].lower()


def match_remote_model(
    models: list[dict[str, Any]],
    *,
    filename: str = '',
    repo_id: str = '',
    path: str = '',
) -> dict[str, Any] | None:
    """Find the same file on the remote catalog by name, then by repo."""
    want = _basename(filename) or _basename(path)
    repo = str(repo_id or '').strip().lower()
    rows = [row for row in models if isinstance(row, dict)]
    if want:
        for row in rows:
            name = _basename(row.get('filename') or row.get('path') or row.get('label') or '')
            if name and name == want:
                return row
    if repo and want:
        for row in rows:
            hay = ' '.join(
                str(row.get(key) or '')
                for key in ('path', 'hf_repo', 'repo_id', 'filename', 'label')
            ).lower()
            if repo in hay and want in hay:
                return row
    return None


def _port_open(port: int, *, timeout: float = 0.4) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection(('127.0.0.1', int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _ssh_executable() -> str:
    found = shutil.which('ssh')
    if found:
        return found
    if sys.platform == 'win32':
        return r'C:\Windows\System32\OpenSSH\ssh.exe'
    return 'ssh'


def ensure_ssh_tunnel(node: dict[str, Any], *, wait_seconds: float = 8.0) -> None:
    """Open the local forward if this node is reached through SSH."""
    host = str(node.get('ssh_host') or '').strip()
    local_port = int(node.get('ssh_local_port') or 0)
    remote_port = int(node.get('ssh_remote_port') or 0)
    if not host or local_port <= 0 or remote_port <= 0:
        return
    if _port_open(local_port):
        return
    with _TUNNEL_LOCK:
        if _port_open(local_port):
            return
        cmd = [
            _ssh_executable(),
            '-N',
            '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=8',
            '-o', 'ExitOnForwardFailure=yes',
            '-L', f'{local_port}:127.0.0.1:{remote_port}',
            host,
        ]
        kwargs: dict[str, Any] = {
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
        }
        if sys.platform == 'win32':
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        subprocess.Popen(cmd, **kwargs)
        deadline = time.time() + max(1.0, float(wait_seconds))
        while time.time() < deadline:
            if _port_open(local_port):
                return
            time.sleep(0.25)
    raise RuntimeError(
        f'Could not open the SSH tunnel to {host}. '
        'Check that this PC can reach that machine.'
    )


def _remote_json(
    node: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    base = str(node.get('base_url') or '').rstrip('/')
    url = f'{base}{path}'
    data = None
    headers = _node_headers(node)
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers = {**headers, 'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        payload: Any = {}
        try:
            payload = json.loads(raw or '{}')
        except json.JSONDecodeError:
            payload = {}
        detail = payload.get('detail') if isinstance(payload, dict) else None
        if exc.code == 409 and isinstance(detail, dict):
            return detail
        if isinstance(detail, dict):
            message = str(detail.get('error') or detail.get('message') or '')
        elif isinstance(detail, str):
            message = detail
        else:
            message = raw.strip()
        raise RuntimeError(message or f'The production Console returned HTTP {exc.code}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            'The production Console is not answering. Start DFlash Console on that PC, then try again.'
        ) from exc
    payload = json.loads(raw or '{}')
    if not isinstance(payload, dict):
        raise RuntimeError('Unexpected reply from the production Console')
    return payload


def _progress_percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    if number <= 1:
        return round(number * 100, 1)
    return round(min(number, 100), 1)


def remote_gpu_devices(*, force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    cached = _GPU_CACHE.get('gpus')
    if (
        not force
        and isinstance(cached, list)
        and (now - float(_GPU_CACHE.get('at') or 0.0)) < _GPU_CACHE_SECONDS
    ):
        return [dict(row) for row in cached]
    devices: list[dict[str, Any]] = []
    for node in list_remote_nodes():
        if node.get('enabled') is False or node.get('share_gpu') is not True:
            continue
        node_id = str(node.get('id') or '')
        label = str(node.get('label') or node_id or 'Remote')
        try:
            ensure_ssh_tunnel(node)
            payload = _remote_json(node, 'GET', '/api/gpu-devices', timeout=8)
            rows = payload.get('gpus') if isinstance(payload.get('gpus'), list) else []
            if not rows:
                raise RuntimeError('no GPUs')
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    remote_index = int(row.get('index'))
                except (TypeError, ValueError):
                    continue
                raw_name = str(row.get('display_name') or row.get('name') or f'GPU {remote_index}')
                name = shared_gpu_label(raw_name, node_id)
                devices.append({
                    'index': f'remote:{node_id}:{remote_index}',
                    'name': raw_name,
                    'display_name': name,
                    'vram_gb': row.get('vram_gb'),
                    'vram_used_gb': row.get('vram_used_gb'),
                    'vram_free_gb': row.get('vram_free_gb'),
                    'remote': True,
                    'node_id': node_id,
                    'remote_index': remote_index,
                })
        except Exception:
            devices.append({
                'index': f'remote:{node_id}:0',
                'name': 'GPU',
                'display_name': shared_gpu_label('GPU', node_id),
                'vram_gb': None,
                'remote': True,
                'node_id': node_id,
                'remote_index': 0,
                'offline': True,
            })
    if devices and not any(row.get('offline') for row in devices):
        _GPU_CACHE['at'] = time.time()
        _GPU_CACHE['gpus'] = [dict(row) for row in devices]
    return devices


def _installed_path(payload: dict[str, Any]) -> str:
    path = str(payload.get('path') or '').strip()
    if path:
        return path
    matches = payload.get('matches')
    if isinstance(matches, list) and matches and isinstance(matches[0], dict):
        return str(matches[0].get('path') or '').strip()
    return ''


def load_model_on_remote_gpu(body: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_remote_gpu_id(str(body.get('gpu_device') or ''))
    if not parsed:
        raise RuntimeError('Choose a production GPU first')
    node_id, remote_index = parsed
    node = get_remote_node(node_id)
    if not node or node.get('share_gpu') is not True:
        raise RuntimeError('That production machine is not set up to share its GPU')
    ensure_ssh_tunnel(node)
    filename = str(body.get('filename') or '').strip() or _basename(str(body.get('path') or ''))
    repo_id = str(body.get('repo_id') or '').strip()
    label = str(body.get('label') or filename or 'Model')
    gpu_label = str(node.get('label') or node_id)

    job_id = str(body.get('download_job_id') or '').strip()
    remote_path = ''
    if job_id:
        job = _remote_json(node, 'GET', f'/api/hf/download/{urllib.parse.quote(job_id, safe="")}', timeout=20)
        status = str(job.get('status') or '').lower()
        if status in {'downloading', 'incomplete'}:
            return {
                'success': True,
                'loaded': False,
                'downloading': True,
                'download_job_id': job_id,
                'progress': _progress_percent(job.get('progress')),
                'gpu_label': gpu_label,
                'message': f'Downloading {filename or label} onto {gpu_label}',
            }
        if status not in {'done', 'complete', 'completed'}:
            raise RuntimeError(str(job.get('error') or 'The download on the production machine failed'))
        remote_path = _installed_path(job)

    if not remote_path:
        catalog = _remote_json(node, 'GET', '/api/models', timeout=40)
        models = catalog.get('models') if isinstance(catalog.get('models'), list) else []
        match = match_remote_model(models, filename=filename, repo_id=repo_id, path=str(body.get('path') or ''))
        if match:
            remote_path = str(match.get('path') or '').strip()
        elif repo_id and filename:
            started = _remote_json(
                node,
                'POST',
                '/api/hf/download',
                {'repo_id': repo_id, 'filename': filename},
                timeout=40,
            )
            if started.get('already_installed'):
                remote_path = _installed_path(started)
            else:
                new_job = str(started.get('id') or started.get('job_id') or '').strip()
                if not new_job:
                    raise RuntimeError('The production machine did not start the download')
                return {
                    'success': True,
                    'loaded': False,
                    'downloading': True,
                    'download_job_id': new_job,
                    'progress': _progress_percent(started.get('progress')),
                    'gpu_label': gpu_label,
                    'message': f'Downloading {filename} onto {gpu_label}',
                }
        else:
            raise RuntimeError(
                f'{label} is not on the {gpu_label} machine, and this Console does not know where to download it.'
            )

    if not remote_path:
        raise RuntimeError(f'{label} is not ready on the {gpu_label} machine yet')

    result = _remote_json(
        node,
        'POST',
        '/api/models/load',
        {
            'path': remote_path,
            'model_id': str(body.get('model_id') or ''),
            'runtime_id': str(body.get('runtime_id') or ''),
            'gpu_device': str(remote_index),
        },
        timeout=120,
    )
    if result.get('success') is False:
        raise RuntimeError(str(result.get('error') or result.get('message') or 'Load failed'))
    return {
        'success': True,
        'loaded': True,
        'downloading': False,
        'path': remote_path,
        'gpu_label': gpu_label,
        'message': f'{label} is loading on {gpu_label}',
        'remote': result,
    }
